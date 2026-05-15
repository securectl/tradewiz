"""
Neural Network Self-Learning Brain for Crypto Bot.

Uses past trade data to train models that improve signal quality:
1. Unsupervised Learning (K-Means clustering) — identifies market regime clusters
   from trade feature vectors to detect which conditions produce winners vs losers.
2. Neural Network (PyTorch MLP) — binary classifier trained on past trades to predict
   whether a signal will be profitable given current indicators.
3. Feature engineering from raw trade + indicator data.

The brain retrains periodically (every 6 hours) on the rolling 30-day trade window.
Models are stored in memory (per-user singleton) and rebuilt on restart.

Falls back gracefully if torch/sklearn aren't available — the bot continues with
its existing rule-based self-learning.
"""

import os
import sys
import json
import logging
import pickle
import threading
from datetime import datetime

import numpy as np

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from db import query, IS_POSTGRES

P = "%s" if IS_POSTGRES else "?"

# Try importing ML libraries — graceful fallback if not installed
_HAS_SKLEARN = False
_HAS_TORCH = False

try:
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler
    _HAS_SKLEARN = True
except ImportError:
    logger.info("scikit-learn not available — clustering disabled")

try:
    import torch
    import torch.nn as nn
    _HAS_TORCH = True
except ImportError:
    logger.info("PyTorch not available — neural network disabled")


# ─── Feature Engineering ────────────────────────────────────────

def _extract_trade_features(trade_row):
    """Extract a numeric feature vector from a closed trade record.

    Features (13-dim):
    [0] side: 1=buy, 0=sell
    [1] entry_price (log-scaled)
    [2] size_usd (entry_price * size)
    [3] stop_loss_pct (distance from entry)
    [4] take_profit_pct (distance from entry)
    [5] hour_of_day (0-23)
    [6] day_of_week (0-6)
    [7] hold_hours (time open)
    [8] strategy_id (hashed to int)
    [9] pnl_pct (realized)
    [10] fee_pct (fee drag)
    [11] sl_tp_ratio (risk:reward)
    [12] was_profitable (1/0) — target label
    """
    try:
        entry = float(trade_row.get("entry_price", 0) or 0)
        exit_p = float(trade_row.get("exit_price", 0) or 0)
        size = float(trade_row.get("size", 0) or 0)
        sl = float(trade_row.get("stop_loss", 0) or 0)
        tp = float(trade_row.get("take_profit", 0) or 0)
        pnl = float(trade_row.get("pnl", 0) or 0)
        pnl_pct = float(trade_row.get("pnl_pct", 0) or 0)
        fee = float(trade_row.get("fee", 0) or 0)
        side = 1.0 if trade_row.get("side") == "buy" else 0.0
        strategy = trade_row.get("strategy", "unknown") or "unknown"

        # Time features
        opened_str = str(trade_row.get("opened_at", ""))
        closed_str = str(trade_row.get("closed_at", ""))
        try:
            opened = datetime.fromisoformat(opened_str.replace("Z", ""))
            hour = float(opened.hour)
            dow = float(opened.weekday())
        except Exception:
            hour, dow = 12.0, 3.0

        try:
            opened = datetime.fromisoformat(opened_str.replace("Z", ""))
            closed = datetime.fromisoformat(closed_str.replace("Z", ""))
            hold_hours = max(0, (closed - opened).total_seconds() / 3600)
        except Exception:
            hold_hours = 4.0

        # Derived features
        sl_pct = abs(entry - sl) / entry * 100 if entry > 0 and sl > 0 else 2.0
        tp_pct = abs(tp - entry) / entry * 100 if entry > 0 and tp > 0 else 5.0
        sl_tp_ratio = sl_pct / tp_pct if tp_pct > 0 else 1.0
        size_usd = entry * size
        fee_pct = fee / size_usd * 100 if size_usd > 0 else 0.16
        log_price = np.log1p(entry)

        # Strategy encoding (simple hash to bounded int)
        strat_map = {
            "macd_cross": 1, "ema_trend": 2, "rsi_reversion": 3,
            "momentum": 4, "bb_reversion": 5, "grid_reversion": 6,
            "trend_dca": 7, "doji_reversal": 8, "pump_on_close": 9,
            "swing_vcp": 10, "swing_htf": 11, "swing_breakout": 12, "swing_trend": 13,
        }
        strat_id = float(strat_map.get(strategy, 0))

        features = np.array([
            side, log_price, min(size_usd, 10000) / 1000,  # normalize
            sl_pct, tp_pct, hour, dow, min(hold_hours, 48),
            strat_id, pnl_pct, fee_pct, sl_tp_ratio,
        ], dtype=np.float32)

        label = 1.0 if pnl > 0 else 0.0

        return features, label

    except Exception as e:
        logger.debug(f"Feature extraction failed: {e}")
        return None, None


def _extract_signal_features(coin_key, side, price, indicators, strategy, sl, tp):
    """Extract features from a LIVE signal (before trade) to predict profitability.

    Must match the same feature layout as _extract_trade_features (minus pnl/fee/hold).
    """
    try:
        side_val = 1.0 if side == "buy" else 0.0
        log_price = np.log1p(price)

        sl_pct = abs(price - sl) / price * 100 if price > 0 and sl > 0 else 2.0
        tp_pct = abs(tp - price) / price * 100 if price > 0 and tp > 0 else 5.0
        sl_tp_ratio = sl_pct / tp_pct if tp_pct > 0 else 1.0

        now = datetime.now()
        hour = float(now.hour)
        dow = float(now.weekday())

        strat_map = {
            "macd_cross": 1, "ema_trend": 2, "rsi_reversion": 3,
            "momentum": 4, "bb_reversion": 5, "grid_reversion": 6,
            "trend_dca": 7, "doji_reversal": 8, "pump_on_close": 9,
            "swing_vcp": 10, "swing_htf": 11, "swing_breakout": 12, "swing_trend": 13,
        }
        strat_id = float(strat_map.get(strategy, 0))

        # Use indicator features for the signal-quality dimensions
        rsi = float(indicators.get("rsi_14", 50))
        macd_h = float(indicators.get("macd_histogram", 0))
        rel_vol = float(indicators.get("relative_volume", 1.0))

        features = np.array([
            side_val, log_price, 0.0,  # size unknown at signal time
            sl_pct, tp_pct, hour, dow, 0.0,  # hold unknown
            strat_id, 0.0, 0.16, sl_tp_ratio,  # pnl unknown, assume avg fee
        ], dtype=np.float32)

        return features

    except Exception as e:
        logger.debug(f"Signal feature extraction failed: {e}")
        return None


# ─── Unsupervised Clustering (Market Regime from Trade Outcomes) ─

class TradeClusterer:
    """K-Means clustering on trade features to identify winning vs losing regimes.

    Clusters trades into K groups. Each cluster gets a win rate label.
    New signals are assigned to the nearest cluster to predict regime quality.
    """

    def __init__(self, n_clusters=4):
        self.n_clusters = n_clusters
        self.model = None
        self.scaler = None
        self.cluster_stats = {}  # {cluster_id: {"win_rate": float, "avg_pnl": float, "count": int}}
        self.trained = False

    def train(self, features, labels):
        if not _HAS_SKLEARN or len(features) < self.n_clusters * 3:
            return False

        try:
            self.scaler = StandardScaler()
            X = self.scaler.fit_transform(features)

            self.model = KMeans(n_clusters=self.n_clusters, random_state=42, n_init=10)
            clusters = self.model.fit_predict(X)

            # Compute stats per cluster
            self.cluster_stats = {}
            for k in range(self.n_clusters):
                mask = clusters == k
                if mask.sum() == 0:
                    continue
                cluster_labels = labels[mask]
                self.cluster_stats[k] = {
                    "win_rate": float(cluster_labels.mean()) * 100,
                    "count": int(mask.sum()),
                    "avg_pnl_pct": float(features[mask, 9].mean()),  # pnl_pct column
                }

            self.trained = True
            return True
        except Exception as e:
            logger.warning(f"Clustering training failed: {e}")
            return False

    def predict_cluster(self, signal_features):
        if not self.trained or self.model is None or self.scaler is None:
            return None

        try:
            X = self.scaler.transform(signal_features.reshape(1, -1))
            cluster = self.model.predict(X)[0]
            return self.cluster_stats.get(cluster)
        except Exception:
            return None


# ─── Neural Network (Trade Profitability Predictor) ─────────────

if _HAS_TORCH:
    class TradeMLP(nn.Module):
        """Simple MLP for binary classification: profitable or not."""
        def __init__(self, input_dim=12, hidden_dim=32):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(input_dim, hidden_dim),
                nn.ReLU(),
                nn.Dropout(0.2),
                nn.Linear(hidden_dim, hidden_dim // 2),
                nn.ReLU(),
                nn.Dropout(0.1),
                nn.Linear(hidden_dim // 2, 1),
                nn.Sigmoid(),
            )

        def forward(self, x):
            return self.net(x)


class NeuralPredictor:
    """Trains a small MLP on past trades to predict signal profitability."""

    def __init__(self):
        self.model = None
        self.scaler = None
        self.trained = False
        self.accuracy = 0.0
        self.train_size = 0

    def train(self, features, labels, epochs=100, lr=0.005):
        if not _HAS_TORCH or len(features) < 20:
            return False

        try:
            self.scaler = StandardScaler()
            X = self.scaler.fit_transform(features).astype(np.float32)
            y = labels.astype(np.float32)

            # Train/val split (80/20)
            n = len(X)
            idx = np.random.permutation(n)
            split = int(n * 0.8)
            X_train, X_val = X[idx[:split]], X[idx[split:]]
            y_train, y_val = y[idx[:split]], y[idx[split:]]

            X_train_t = torch.tensor(X_train)
            y_train_t = torch.tensor(y_train).unsqueeze(1)
            X_val_t = torch.tensor(X_val)
            y_val_t = torch.tensor(y_val).unsqueeze(1)

            self.model = TradeMLP(input_dim=X.shape[1])
            optimizer = torch.optim.Adam(self.model.parameters(), lr=lr, weight_decay=1e-4)
            loss_fn = nn.BCELoss()

            # Class weight for imbalanced data (typically more losses than wins)
            pos_count = y_train.sum()
            neg_count = len(y_train) - pos_count
            if pos_count > 0 and neg_count > 0:
                pos_weight = neg_count / pos_count
                loss_fn = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([pos_weight]))
                # Need to remove sigmoid from model for BCEWithLogitsLoss
                self.model = TradeMLP(input_dim=X.shape[1])
                # Replace last sigmoid
                self.model.net[-1] = nn.Identity()
                optimizer = torch.optim.Adam(self.model.parameters(), lr=lr, weight_decay=1e-4)

            best_val_loss = float("inf")
            patience, patience_counter = 15, 0

            self.model.train()
            for epoch in range(epochs):
                optimizer.zero_grad()
                pred = self.model(X_train_t)
                loss = loss_fn(pred, y_train_t)
                loss.backward()
                optimizer.step()

                # Validation
                if len(X_val) > 0 and epoch % 5 == 0:
                    self.model.eval()
                    with torch.no_grad():
                        val_pred = self.model(X_val_t)
                        val_loss = loss_fn(val_pred, y_val_t).item()
                    self.model.train()

                    if val_loss < best_val_loss:
                        best_val_loss = val_loss
                        patience_counter = 0
                    else:
                        patience_counter += 1
                        if patience_counter >= patience:
                            break

            # Compute validation accuracy
            self.model.eval()
            with torch.no_grad():
                if len(X_val) > 0:
                    val_pred = torch.sigmoid(self.model(X_val_t))
                    val_binary = (val_pred > 0.5).float()
                    self.accuracy = (val_binary == y_val_t).float().mean().item() * 100
                else:
                    self.accuracy = 0

            self.trained = True
            self.train_size = n
            return True

        except Exception as e:
            logger.warning(f"Neural network training failed: {e}")
            return False

    def predict_probability(self, signal_features):
        """Predict probability that a signal will be profitable (0-1)."""
        if not self.trained or self.model is None or self.scaler is None:
            return None

        try:
            X = self.scaler.transform(signal_features.reshape(1, -1)).astype(np.float32)
            X_t = torch.tensor(X)
            self.model.eval()
            with torch.no_grad():
                prob = torch.sigmoid(self.model(X_t)).item()
            return prob
        except Exception:
            return None


# ─── Main Brain Class (Orchestrates Everything) ─────────────────

class MLBrain:
    """Central ML brain that manages clustering + neural network models.

    Usage:
        brain = get_brain(user_id)
        brain.maybe_retrain()  # Called each scan cycle
        score = brain.score_signal(coin, side, price, indicators, strategy, sl, tp)
        # score is None (no model) or 0-100 (confidence adjustment)
    """

    RETRAIN_INTERVAL = 6 * 3600  # 6 hours

    def __init__(self, user_id):
        self.user_id = user_id
        self.clusterer = TradeClusterer(n_clusters=4)
        self.neural = NeuralPredictor()
        self.last_train_time = None
        self.train_count = 0
        self._lock = threading.Lock()

    def maybe_retrain(self):
        """Retrain models if enough time has passed. Non-blocking."""
        now = datetime.now()
        if self.last_train_time and (now - self.last_train_time).total_seconds() < self.RETRAIN_INTERVAL:
            return

        # Run training in background to not block scan cycle
        t = threading.Thread(target=self._retrain, daemon=True)
        t.start()

    def _retrain(self):
        with self._lock:
            try:
                rows = query(
                    f"SELECT * FROM bot_trades "
                    f"WHERE user_id = {P} AND status = 'closed' AND asset_type = 'crypto' "
                    f"AND closed_at >= NOW() - INTERVAL '30 days' ORDER BY closed_at",
                    (self.user_id,),
                )

                if not rows or len(rows) < 20:
                    logger.info(f"[ML Brain] Not enough trades for training ({len(rows) if rows else 0}/20 min)")
                    self.last_train_time = datetime.now()
                    return

                features_list = []
                labels_list = []
                for r in rows:
                    feat, label = _extract_trade_features(r)
                    if feat is not None:
                        features_list.append(feat)
                        labels_list.append(label)

                if len(features_list) < 20:
                    self.last_train_time = datetime.now()
                    return

                X = np.array(features_list)
                y = np.array(labels_list)

                # Train clustering
                c_ok = self.clusterer.train(X, y)
                if c_ok:
                    stats_str = " | ".join(
                        f"C{k}: WR={v['win_rate']:.0f}% ({v['count']} trades)"
                        for k, v in sorted(self.clusterer.cluster_stats.items())
                    )
                    logger.info(f"[ML Brain] Clustering trained on {len(X)} trades: {stats_str}")

                # Train neural network
                n_ok = self.neural.train(X, y)
                if n_ok:
                    logger.info(f"[ML Brain] Neural net trained on {len(X)} trades — val accuracy: {self.neural.accuracy:.1f}%")

                self.train_count += 1
                self.last_train_time = datetime.now()
                logger.info(f"[ML Brain] Training complete (round #{self.train_count})")

            except Exception as e:
                logger.warning(f"[ML Brain] Training failed: {e}")
                self.last_train_time = datetime.now()

    def score_signal(self, coin_key, side, price, indicators, strategy, stop_loss, take_profit):
        """Score a potential trade signal using ML models.

        Returns:
            dict with:
                "ml_score": 0-100 (predicted win probability * 100), or None if no model
                "cluster_info": cluster stats for this signal's regime, or None
                "recommendation": "STRONG_BUY" / "BUY" / "WEAK" / "AVOID" / None
                "neural_prob": raw probability from neural net
        """
        result = {
            "ml_score": None,
            "cluster_info": None,
            "recommendation": None,
            "neural_prob": None,
        }

        features = _extract_signal_features(coin_key, side, price, indicators, strategy, stop_loss, take_profit)
        if features is None:
            return result

        # Cluster prediction
        cluster_stats = self.clusterer.predict_cluster(features)
        if cluster_stats:
            result["cluster_info"] = cluster_stats

        # Neural network prediction
        prob = self.neural.predict_probability(features)
        if prob is not None:
            result["neural_prob"] = prob
            result["ml_score"] = round(prob * 100, 1)

        # Combined recommendation
        if result["ml_score"] is not None:
            score = result["ml_score"]
            cluster_wr = cluster_stats["win_rate"] if cluster_stats else 50

            # Blend: 60% neural + 40% cluster
            if cluster_stats:
                blended = score * 0.6 + cluster_wr * 0.4
            else:
                blended = score

            if blended >= 65:
                result["recommendation"] = "STRONG_BUY"
            elif blended >= 50:
                result["recommendation"] = "BUY"
            elif blended >= 35:
                result["recommendation"] = "WEAK"
            else:
                result["recommendation"] = "AVOID"

            result["ml_score"] = round(blended, 1)

        return result

    def get_strategy_rankings(self):
        """Return strategy performance rankings from cluster analysis.

        Used by adaptive params to inform strategy selection.
        """
        if not self.clusterer.trained:
            return {}

        try:
            rows = query(
                f"SELECT strategy, COUNT(*) as cnt, "
                f"SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins, "
                f"SUM(pnl) as total_pnl, AVG(pnl) as avg_pnl "
                f"FROM bot_trades WHERE user_id = {P} AND status = 'closed' AND asset_type = 'crypto' "
                f"AND closed_at >= NOW() - INTERVAL '14 days' "
                f"GROUP BY strategy ORDER BY total_pnl DESC",
                (self.user_id,),
            )
            if not rows:
                return {}

            rankings = {}
            for r in rows:
                strat = r["strategy"]
                cnt = int(r["cnt"])
                wins = int(r["wins"])
                wr = wins / cnt * 100 if cnt > 0 else 0
                rankings[strat] = {
                    "count": cnt,
                    "win_rate": round(wr, 1),
                    "total_pnl": round(float(r["total_pnl"] or 0), 2),
                    "avg_pnl": round(float(r["avg_pnl"] or 0), 2),
                }
            return rankings
        except Exception:
            return {}

    def get_optimal_params(self):
        """Use ML insights to suggest optimal trading parameters.

        Returns adjustments to position sizing, SL/TP ratios, strategy weights.
        """
        params = {
            "sl_multiplier": 1.2,  # default
            "tp_multiplier": 4.0,  # default
            "strategies_to_disable": [],
            "strategies_to_boost": [],
            "position_scale_override": None,
        }

        rankings = self.get_strategy_rankings()
        if not rankings:
            return params

        # Identify strategies to disable (negative total P&L with enough trades)
        for strat, stats in rankings.items():
            if stats["count"] >= 4 and stats["total_pnl"] < -20 and stats["win_rate"] < 40:
                params["strategies_to_disable"].append(strat)
            elif stats["count"] >= 5 and stats["total_pnl"] > 0 and stats["win_rate"] > 55:
                params["strategies_to_boost"].append(strat)

        # Analyze winning trades to find optimal SL/TP
        try:
            win_rows = query(
                f"SELECT stop_loss, take_profit, entry_price, pnl_pct FROM bot_trades "
                f"WHERE user_id = {P} AND status = 'closed' AND asset_type = 'crypto' "
                f"AND pnl > 0 AND closed_at >= NOW() - INTERVAL '14 days'",
                (self.user_id,),
            )
            if win_rows and len(win_rows) >= 5:
                # Average SL/TP distance as % of entry for winners
                sl_pcts = []
                tp_pcts = []
                for r in win_rows:
                    ep = float(r["entry_price"] or 0)
                    sl_val = float(r["stop_loss"] or 0)
                    tp_val = float(r["take_profit"] or 0)
                    if ep > 0:
                        if sl_val > 0:
                            sl_pcts.append(abs(ep - sl_val) / ep * 100)
                        if tp_val > 0:
                            tp_pcts.append(abs(tp_val - ep) / ep * 100)
                if sl_pcts:
                    # Convert winning SL% back to ATR multiplier (approx: 1 ATR ~= 1.5% for crypto)
                    avg_sl_pct = np.median(sl_pcts)
                    params["sl_multiplier"] = round(max(0.8, min(2.5, avg_sl_pct / 1.5)), 2)
                if tp_pcts:
                    avg_tp_pct = np.median(tp_pcts)
                    params["tp_multiplier"] = round(max(2.0, min(8.0, avg_tp_pct / 1.5)), 2)
        except Exception:
            pass

        return params

    def is_ready(self):
        return self.clusterer.trained or self.neural.trained


# ─── Per-user Brain Instances ───────────────────────────────────

_brains = {}
_brains_lock = threading.Lock()


def get_brain(user_id):
    with _brains_lock:
        if user_id not in _brains:
            _brains[user_id] = MLBrain(user_id)
        return _brains[user_id]
