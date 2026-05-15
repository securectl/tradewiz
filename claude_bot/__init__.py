"""Claude Trading Bot — scalp-and-rotate with re-entry watchlist.

Stocks-only paper trading bot inspired by the OpenClaw scalping pattern:
when a position exits (small stop or quick take-profit), keep the ticker on
a re-entry watchlist for up to 3 days so the bot can scalp-and-rotate back
in if the price stabilizes.
"""
