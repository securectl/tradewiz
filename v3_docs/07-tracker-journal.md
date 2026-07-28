# 07 · Tracker & Journal

The **Tracker** is where you record your own trades, set profit goals, and review what the
bots have been doing — all in one place.

---

## Bot Trades (top panel)

A full-width feed of every trade your bots have taken, with filter pills for
**Crypto · Stock · Watchdog · Claude**. Columns: date, symbol, action (BUY/SELL), price,
quantity, P&L, and status. This is read-only — it mirrors the live bot activity covered in
the [Crypto](10-crypto-bot.md), [Stock](11-stock-bot.md), and
[Claude/ThunderBot](12-claude-thunderbot.md) chapters.

---

## Trade Journal (left panel)

Log your **own** trades and notes.

### Quick add
- Enter a **ticker** and a **note**, then click **Add Note** for a fast journal entry.
- Toggle **Trade** to expand the full fields: **action** (BUY/SELL), **entry price**,
  **exit price**, and **shares**. TradeWiz computes P&L (dollars and %).

### Journal list
Your entries appear newest-first with date, ticker, note, and any entry/exit prices. You
can edit or delete entries at any time.

*(Routes: `GET/POST /api/journal`, `PUT /api/journal/<id>`, `DELETE /api/journal/<id>`.)*

### What to capture
Good journaling is the fastest way to improve. For each trade, note:
- The **setup** (pattern, screener verdict, or bot strategy),
- **Why** you entered and your planned stop/target,
- The **market regime** at the time,
- The **outcome** and one lesson.

---

## Goal Dashboard (right panel)

Set a **weekly or monthly profit goal** and watch your progress:
- A progress bar toward the target,
- Trades counted toward the goal,
- Historical goal attainment.

This complements the **Daily Goal** tiles the bots track (a $500/day default target per
bot — see the bot chapters).

---

## Search History

Separate from the journal, the header **History** button stores your recent **Analyzer**
queries (ticker + period + interval) for 3 days so you can re-open any analysis with one
click. *(Routes: `GET /api/history`, `GET /api/history/<id>`, `DELETE /api/history/<id>`.)*
