"""Parse broker positions CSV (Fidelity / Schwab) into normalized holdings.

Both brokers let a user export "Positions" as CSV. Column names differ and files
carry header/footer noise (account totals, cash rows, disclaimers), so we match
columns fuzzily and keep only real equity/ETF positions.

Returns a list of {symbol, shares, cost_basis} dicts. Never raises.
"""

import csv
import io
import re

# Rows/symbols that aren't tradable equity positions we can analyze.
_SKIP_SYMBOLS = {
    "", "CASH", "SPAXX", "FDRXX", "FZFXX", "SWVXX", "PENDING ACTIVITY",
    "ACCOUNT TOTAL", "TOTAL", "CORE**", "N/A", "--",
}
_SKIP_RE = re.compile(r"(money market|pending|account total|cash|as of|brokerage)", re.I)


def _num(v):
    """Parse a currency/quantity string ('$1,234.50', '(12.3)', '1,000') → float."""
    if v is None:
        return None
    s = str(v).strip().replace("$", "").replace(",", "").replace("%", "")
    if s in ("", "--", "N/A", "n/a"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        f = float(s)
        return -f if neg else f
    except ValueError:
        return None


def _pick(header, *needles):
    """Index of the first header column whose name contains all needles (any group)."""
    low = [h.strip().lower() for h in header]
    for group in needles:
        want = group if isinstance(group, tuple) else (group,)
        for i, h in enumerate(low):
            if all(w in h for w in want):
                return i
    return None


def parse_positions_csv(text):
    """Parse pasted/uploaded broker CSV text into normalized holdings."""
    if not text or not text.strip():
        return []
    # Find the header row (the first line that has a Symbol-like column).
    lines = [ln for ln in text.splitlines() if ln.strip()]
    reader_rows = list(csv.reader(io.StringIO("\n".join(lines))))
    header_idx = None
    for i, row in enumerate(reader_rows):
        joined = ",".join(c.lower() for c in row)
        if ("symbol" in joined or "ticker" in joined) and (
                "quantity" in joined or "shares" in joined or "qty" in joined):
            header_idx = i
            break
    if header_idx is None:
        return []

    header = reader_rows[header_idx]
    i_sym = _pick(header, "symbol", "ticker")
    i_qty = _pick(header, "quantity", "shares", "qty")
    # Prefer total cost basis; fall back to average cost × shares later.
    i_cost = _pick(header, ("cost", "basis", "total"), ("cost", "basis"), "cost basis")
    i_avg = _pick(header, ("average", "cost"), ("avg", "cost"))
    if i_sym is None or i_qty is None:
        return []

    out = {}
    for row in reader_rows[header_idx + 1:]:
        if not row or len(row) <= i_sym:
            continue
        sym = (row[i_sym] or "").strip().upper()
        if not sym or sym in _SKIP_SYMBOLS or _SKIP_RE.search(sym):
            continue
        # Symbols are 1–6 letters (allow a dot for class shares, e.g. BRK.B).
        if not re.fullmatch(r"[A-Z]{1,6}(\.[A-Z])?", sym):
            continue
        shares = _num(row[i_qty]) if len(row) > i_qty else None
        if not shares or shares <= 0:
            continue
        cost = _num(row[i_cost]) if (i_cost is not None and len(row) > i_cost) else None
        if cost is None and i_avg is not None and len(row) > i_avg:
            avg = _num(row[i_avg])
            cost = round(avg * shares, 2) if avg else None
        out[sym] = {"symbol": sym, "shares": round(shares, 4),
                    "cost_basis": round(cost, 2) if cost else None}
    return list(out.values())


def detect_source(text):
    """Best-effort broker name from the CSV content."""
    t = (text or "").lower()
    if "fidelity" in t:
        return "fidelity"
    if "schwab" in t:
        return "schwab"
    return "csv"
