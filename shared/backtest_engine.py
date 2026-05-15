"""Backtest engine — vectorized strategy evaluation with realistic friction.

Design goals:
  - One-bar lag: signal on close of day N → entry at open of day N+1 (no look-ahead)
  - Compounding equity: position size = equity * risk_pct / (entry - stop)
  - Friction: spread + slippage + fees, applied to every fill
  - Regime classification per entry day using SPY 200d SMA + 10d return
  - Per-trade attribution: regime, exit reason, bars held, R-multiple

Strategies plug in as a callable detector_fn(df_window, n) -> setup dict | None.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Callable, Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


# ─── Friction model ─────────────────────────────────────────────────────

@dataclass
class FrictionModel:
    """Simulates real-world execution costs.

    Defaults aimed at retail US equities:
      spread_bps   — half-spread paid on each side (10 bps full spread → 5 bps each side)
      slippage_bps — slippage from market impact / latency
      fee_bps      — commissions (0 for Alpaca paper/Robinhood; ~10 bps for some)

    For crypto, double these (live BloFin/Binance round trip is 30-100 bps on majors).
    """
    spread_bps: float = 5.0       # half-spread per side
    slippage_bps: float = 5.0     # per side
    fee_bps: float = 0.0          # per side

    @property
    def per_side_bps(self) -> float:
        return self.spread_bps + self.slippage_bps + self.fee_bps

    def fill_buy(self, price: float) -> float:
        """Adjust buy price upward by friction."""
        return price * (1.0 + self.per_side_bps / 10000.0)

    def fill_sell(self, price: float) -> float:
        """Adjust sell price downward by friction."""
        return price * (1.0 - self.per_side_bps / 10000.0)


CRYPTO_FRICTION = FrictionModel(spread_bps=20.0, slippage_bps=10.0, fee_bps=10.0)


# ─── Trade and report dataclasses ───────────────────────────────────────

@dataclass
class Trade:
    ticker: str
    side: str                # "long" or "short"
    entry_date: str
    entry_price: float       # post-friction fill
    exit_date: str
    exit_price: float        # post-friction fill
    raw_entry: float
    raw_exit: float
    stop_loss: float
    take_profit: float
    pnl_pct: float           # net of friction
    pnl_dollars: float
    r_multiple: float        # pnl / risk
    exit_reason: str         # "stop", "target", "time", "trail"
    bars_held: int
    strategy: str
    regime_at_entry: str


@dataclass
class BacktestReport:
    strategy: str
    universe: list[str]
    start_date: str
    end_date: str
    starting_equity: float
    ending_equity: float
    total_return_pct: float
    cagr_pct: float
    sharpe: float
    max_drawdown_pct: float
    win_rate: float
    avg_winner_pct: float
    avg_loser_pct: float
    profit_factor: float
    expectancy_pct: float
    trade_count: int
    equity_curve: list[dict]  # [{date, equity}]
    trades: list[dict]
    regime_stats: dict        # {bull|chop|down: {trades, win_rate, avg_pnl_pct, expectancy_pct}}
    config: dict
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    def summary_text(self) -> str:
        lines = [
            f"=== Backtest: {self.strategy} ===",
            f"Universe: {len(self.universe)} tickers, {self.start_date} to {self.end_date}",
            f"Trades: {self.trade_count}  Win rate: {self.win_rate:.1f}%",
            f"Profit factor: {self.profit_factor:.2f}  Expectancy: {self.expectancy_pct:+.2f}%",
            f"Avg winner: +{self.avg_winner_pct:.2f}%  Avg loser: {self.avg_loser_pct:.2f}%",
            f"Return: {self.total_return_pct:+.1f}%  CAGR: {self.cagr_pct:+.1f}%",
            f"Sharpe: {self.sharpe:.2f}  Max DD: -{self.max_drawdown_pct:.1f}%",
            "By regime:",
        ]
        for regime, st in self.regime_stats.items():
            lines.append(
                f"  {regime}: {st['trades']} trades, "
                f"WR {st['win_rate']:.1f}%, "
                f"avg {st['avg_pnl_pct']:+.2f}%, "
                f"exp {st['expectancy_pct']:+.2f}%"
            )
        return "\n".join(lines)


# ─── Regime classifier ──────────────────────────────────────────────────

def build_regime_series(spy_df: pd.DataFrame) -> pd.Series:
    """Classify each trading day as bull / chop / down using SPY structure.

      bull: SPY > 200d SMA AND 10d return > -2% AND not crashing
      down: SPY < 200d SMA OR 10d return < -5%
      chop: everything else
    """
    close = spy_df["Close"].values.astype(float)
    sma_200 = pd.Series(close).rolling(200, min_periods=50).mean().values
    ret_10 = pd.Series(close).pct_change(10).values * 100

    regimes = []
    for i in range(len(close)):
        if np.isnan(sma_200[i]) or np.isnan(ret_10[i]):
            regimes.append("unknown")
        elif close[i] < sma_200[i] or ret_10[i] < -5:
            regimes.append("down")
        elif close[i] > sma_200[i] and abs(ret_10[i]) < 2:
            regimes.append("chop")
        elif close[i] > sma_200[i]:
            regimes.append("bull")
        else:
            regimes.append("chop")

    return pd.Series(regimes, index=spy_df.index)


# ─── Engine core ────────────────────────────────────────────────────────

def _download_universe(tickers: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """Batch-download daily candles for a universe. Returns {ticker: df}."""
    if not tickers:
        return {}
    out = {}
    batch = 50
    for i in range(0, len(tickers), batch):
        chunk = tickers[i:i + batch]
        try:
            data = yf.download(" ".join(chunk), start=start, end=end,
                               interval="1d", progress=False, threads=True,
                               group_by="ticker", auto_adjust=True)
            if data is None or data.empty:
                continue
            single = (len(chunk) == 1)
            for t in chunk:
                try:
                    if single:
                        df = data
                    else:
                        if t not in data.columns.get_level_values(0):
                            continue
                        df = data[t]
                    df = df.dropna()
                    if len(df) < 60:
                        continue
                    out[t] = df
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"download batch failed: {e}")
    return out


def run_backtest(
    detector_fn: Callable,
    universe: list[str],
    *,
    strategy_name: str,
    start_date: str,
    end_date: str,
    starting_equity: float = 100_000.0,
    risk_pct: float = 1.0,
    max_positions: int = 5,
    max_hold_days: int = 60,
    trail_after_pct: Optional[float] = 20.0,
    trail_lookback_days: int = 20,
    friction: Optional[FrictionModel] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> BacktestReport:
    """Run a backtest of `detector_fn` across `universe` between dates.

    detector_fn must accept (close, high, low, volume, n) and return either None
    or a dict with at least: entry_price, stop_loss, take_profit. The contract
    matches the existing detectors in analysis_engine.py.

    Compounding model: equity grows as trades close. Position size is computed
    as equity * (risk_pct/100) / (entry - stop). Cap: max_positions concurrent.
    """
    friction = friction or FrictionModel()

    # Pull universe + benchmark for regime classification
    logger.info(f"[BT] downloading {len(universe)} tickers + SPY...")
    data = _download_universe(universe + ["SPY"], start_date, end_date)
    spy_df = data.pop("SPY", None)
    if spy_df is None or spy_df.empty:
        raise RuntimeError("Could not load SPY for regime classification")

    regime_series = build_regime_series(spy_df)

    # Trading calendar: union of dates across universe + SPY
    all_dates = sorted(set().union(*[df.index for df in data.values()]))
    if not all_dates:
        raise RuntimeError("No data after universe download")

    equity = starting_equity
    open_positions: dict[str, dict] = {}     # ticker -> position dict
    closed: list[Trade] = []
    equity_curve: list[dict] = []
    daily_returns: list[float] = []

    last_equity = starting_equity

    for di, date in enumerate(all_dates):
        regime = regime_series.get(date, "unknown") if date in regime_series.index else "unknown"

        # ── 1. Check exits on existing positions (using THIS day's high/low) ─
        for ticker in list(open_positions.keys()):
            pos = open_positions[ticker]
            df = data.get(ticker)
            if df is None or date not in df.index:
                continue
            row = df.loc[date]
            high = float(row["High"])
            low = float(row["Low"])
            close = float(row["Close"])

            exit_price = None
            exit_reason = None

            # Stop-loss hit during the day
            if low <= pos["stop_loss"]:
                exit_price = friction.fill_sell(pos["stop_loss"])
                exit_reason = "stop"
            # Take-profit hit during the day
            elif high >= pos["take_profit"]:
                exit_price = friction.fill_sell(pos["take_profit"])
                exit_reason = "target"
            else:
                # Trailing stop: after +trail_after_pct, trail at lookback-day low
                bars_held = pos["bars_held"] + 1
                pnl_so_far = (close / pos["entry_price"] - 1) * 100
                if (trail_after_pct is not None and pnl_so_far >= trail_after_pct
                        and bars_held >= trail_lookback_days):
                    # Look-back trailing stop = lowest low over last N bars
                    df_idx = df.index.get_loc(date)
                    start_lb = max(0, df_idx - trail_lookback_days)
                    trail_stop = float(df["Low"].iloc[start_lb:df_idx].min())
                    if low <= trail_stop:
                        exit_price = friction.fill_sell(trail_stop)
                        exit_reason = "trail"

                # Time exit
                if exit_price is None and bars_held >= max_hold_days:
                    exit_price = friction.fill_sell(close)
                    exit_reason = "time"

                pos["bars_held"] = bars_held

            if exit_price is not None:
                shares = pos["shares"]
                pnl_dollars = (exit_price - pos["entry_price"]) * shares
                pnl_pct = (exit_price / pos["entry_price"] - 1) * 100
                risk_per_share = pos["entry_price"] - pos["stop_loss"]
                r_mult = pnl_dollars / (shares * risk_per_share) if risk_per_share > 0 else 0.0

                trade = Trade(
                    ticker=ticker,
                    side="long",
                    entry_date=pos["entry_date"],
                    entry_price=pos["entry_price"],
                    exit_date=str(date.date() if hasattr(date, "date") else date),
                    exit_price=exit_price,
                    raw_entry=pos["raw_entry"],
                    raw_exit=close,
                    stop_loss=pos["stop_loss"],
                    take_profit=pos["take_profit"],
                    pnl_pct=round(pnl_pct, 2),
                    pnl_dollars=round(pnl_dollars, 2),
                    r_multiple=round(r_mult, 2),
                    exit_reason=exit_reason,
                    bars_held=pos["bars_held"],
                    strategy=strategy_name,
                    regime_at_entry=pos["regime_at_entry"],
                )
                closed.append(trade)
                equity += pnl_dollars
                del open_positions[ticker]

        # ── 2. Look for new entries (only if room) ─────────────────────────
        if len(open_positions) < max_positions:
            for ticker, df in data.items():
                if ticker in open_positions:
                    continue
                if date not in df.index:
                    continue
                idx = df.index.get_loc(date)
                if idx < 60:
                    continue

                # Use ONLY data up to and including today to generate signal
                window = df.iloc[: idx + 1]
                close_arr = window["Close"].values.astype(float)
                high_arr = window["High"].values.astype(float)
                low_arr = window["Low"].values.astype(float)
                vol_arr = window["Volume"].values.astype(float)
                n = len(window)

                try:
                    setup = detector_fn(close_arr, high_arr, low_arr, vol_arr, n)
                except Exception:
                    continue
                if not setup:
                    continue

                # Entry on NEXT day's open (no look-ahead)
                if idx + 1 >= len(df):
                    continue
                next_open_raw = float(df.iloc[idx + 1]["Open"])
                entry_price = friction.fill_buy(next_open_raw)
                stop_loss = float(setup.get("stop_loss", 0))
                take_profit = float(setup.get("take_profit", 0))

                # Skip degenerate setups
                if stop_loss >= entry_price or take_profit <= entry_price:
                    continue

                # Risk-based position sizing
                risk_per_share = entry_price - stop_loss
                if risk_per_share <= 0:
                    continue
                risk_dollars = equity * (risk_pct / 100.0)
                shares = max(1, int(risk_dollars / risk_per_share))
                # Position size cap: 20% of equity in any one name
                max_position_dollars = equity * 0.20
                if shares * entry_price > max_position_dollars:
                    shares = int(max_position_dollars / entry_price)
                if shares < 1:
                    continue

                next_date = df.index[idx + 1]
                open_positions[ticker] = {
                    "entry_date": str(next_date.date() if hasattr(next_date, "date") else next_date),
                    "entry_price": entry_price,
                    "raw_entry": next_open_raw,
                    "stop_loss": stop_loss,
                    "take_profit": take_profit,
                    "shares": shares,
                    "bars_held": 0,
                    "regime_at_entry": regime,
                }

                if len(open_positions) >= max_positions:
                    break

        # ── 3. Mark-to-market for equity curve ─────────────────────────────
        unrealized = 0.0
        for ticker, pos in open_positions.items():
            df = data.get(ticker)
            if df is None or date not in df.index:
                continue
            close = float(df.loc[date]["Close"])
            unrealized += (close - pos["entry_price"]) * pos["shares"]

        mtm_equity = equity + unrealized
        equity_curve.append({
            "date": str(date.date() if hasattr(date, "date") else date),
            "equity": round(mtm_equity, 2),
            "regime": regime,
        })
        daily_returns.append((mtm_equity / last_equity - 1) if last_equity > 0 else 0.0)
        last_equity = mtm_equity

        if progress_cb and di % 50 == 0:
            progress_cb(di, len(all_dates))

    # ── Force-close remaining positions at last close ─────────────────────
    if open_positions:
        last_date = all_dates[-1]
        for ticker, pos in list(open_positions.items()):
            df = data.get(ticker)
            if df is None or last_date not in df.index:
                continue
            close = float(df.loc[last_date]["Close"])
            exit_price = friction.fill_sell(close)
            shares = pos["shares"]
            pnl_dollars = (exit_price - pos["entry_price"]) * shares
            pnl_pct = (exit_price / pos["entry_price"] - 1) * 100
            risk_per_share = pos["entry_price"] - pos["stop_loss"]
            r_mult = pnl_dollars / (shares * risk_per_share) if risk_per_share > 0 else 0.0
            closed.append(Trade(
                ticker=ticker, side="long",
                entry_date=pos["entry_date"],
                entry_price=pos["entry_price"],
                exit_date=str(last_date.date() if hasattr(last_date, "date") else last_date),
                exit_price=exit_price,
                raw_entry=pos["raw_entry"], raw_exit=close,
                stop_loss=pos["stop_loss"], take_profit=pos["take_profit"],
                pnl_pct=round(pnl_pct, 2), pnl_dollars=round(pnl_dollars, 2),
                r_multiple=round(r_mult, 2),
                exit_reason="forced_close",
                bars_held=pos["bars_held"],
                strategy=strategy_name,
                regime_at_entry=pos["regime_at_entry"],
            ))
            equity += pnl_dollars

    return _build_report(
        closed, equity_curve, daily_returns,
        starting_equity, equity,
        strategy_name, universe, start_date, end_date,
        config={
            "risk_pct": risk_pct,
            "max_positions": max_positions,
            "max_hold_days": max_hold_days,
            "trail_after_pct": trail_after_pct,
            "trail_lookback_days": trail_lookback_days,
            "friction": asdict(friction),
        },
    )


def _build_report(
    trades: list[Trade],
    equity_curve: list[dict],
    daily_returns: list[float],
    starting_equity: float,
    ending_equity: float,
    strategy: str,
    universe: list[str],
    start_date: str,
    end_date: str,
    config: dict,
) -> BacktestReport:
    n = len(trades)
    wins = [t for t in trades if t.pnl_pct > 0]
    losses = [t for t in trades if t.pnl_pct <= 0]

    # CAGR
    try:
        d0 = datetime.fromisoformat(start_date)
        d1 = datetime.fromisoformat(end_date)
        years = max((d1 - d0).days / 365.25, 0.01)
    except Exception:
        years = 1.0
    total_return = (ending_equity / starting_equity - 1) * 100
    cagr = ((ending_equity / starting_equity) ** (1 / years) - 1) * 100 if starting_equity > 0 else 0

    # Sharpe (annualized, assume 0% rf, 252 trading days)
    if daily_returns:
        arr = np.array(daily_returns)
        mean_r = float(np.mean(arr))
        std_r = float(np.std(arr))
        sharpe = (mean_r / std_r) * np.sqrt(252) if std_r > 1e-9 else 0.0
    else:
        sharpe = 0.0

    # Max drawdown from equity curve
    if equity_curve:
        eq = np.array([p["equity"] for p in equity_curve])
        peaks = np.maximum.accumulate(eq)
        dd = (eq - peaks) / peaks
        max_dd_pct = abs(float(dd.min())) * 100
    else:
        max_dd_pct = 0.0

    # Profit factor / expectancy
    gross_win = sum(t.pnl_dollars for t in wins) if wins else 0
    gross_loss = abs(sum(t.pnl_dollars for t in losses)) if losses else 0
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)
    avg_winner = float(np.mean([t.pnl_pct for t in wins])) if wins else 0
    avg_loser = float(np.mean([t.pnl_pct for t in losses])) if losses else 0
    win_rate = len(wins) / n * 100 if n > 0 else 0
    expectancy = (win_rate / 100) * avg_winner + (1 - win_rate / 100) * avg_loser

    # Regime breakdown
    regime_stats = {}
    for regime in ["bull", "chop", "down", "unknown"]:
        rt = [t for t in trades if t.regime_at_entry == regime]
        if not rt:
            continue
        rw = [t for t in rt if t.pnl_pct > 0]
        rwr = len(rw) / len(rt) * 100
        ravg = float(np.mean([t.pnl_pct for t in rt]))
        rwa = float(np.mean([t.pnl_pct for t in rw])) if rw else 0
        rl = [t for t in rt if t.pnl_pct <= 0]
        rla = float(np.mean([t.pnl_pct for t in rl])) if rl else 0
        regime_stats[regime] = {
            "trades": len(rt),
            "win_rate": round(rwr, 1),
            "avg_pnl_pct": round(ravg, 2),
            "expectancy_pct": round((rwr / 100) * rwa + (1 - rwr / 100) * rla, 2),
        }

    return BacktestReport(
        strategy=strategy,
        universe=universe,
        start_date=start_date,
        end_date=end_date,
        starting_equity=starting_equity,
        ending_equity=round(ending_equity, 2),
        total_return_pct=round(total_return, 2),
        cagr_pct=round(cagr, 2),
        sharpe=round(sharpe, 2),
        max_drawdown_pct=round(max_dd_pct, 2),
        win_rate=round(win_rate, 1),
        avg_winner_pct=round(avg_winner, 2),
        avg_loser_pct=round(avg_loser, 2),
        profit_factor=round(profit_factor, 2) if profit_factor != float("inf") else 999.99,
        expectancy_pct=round(expectancy, 2),
        trade_count=n,
        equity_curve=equity_curve,
        trades=[asdict(t) for t in trades],
        regime_stats=regime_stats,
        config=config,
    )
