# Alpaca Dual-Model Intraday Lab

A local, **paper-trading-only** A/B intraday research workstation built around two strategies running against the same market session.

- **Model A — CONTROL:** Opening Range / VWAP.
- **Model B — CHALLENGER:** Momentum Catalyst.

The application is intentionally locked to Alpaca paper trading.

## Session capital: automatic 50/50 split

The live engine no longer assumes a $100,000 account.

When **Start Session** is pressed, the engine reads the actual Alpaca paper account equity and freezes that day's experiment allocation:

- $100,000 equity → Model A $50,000 / Model B $50,000
- $83,421.17 equity → Model A $41,710.59 / Model B $41,710.58
- $40,000 equity → Model A $20,000 / Model B $20,000

The allocation is frozen for the trading day so one model's P&L cannot silently change the other model's starting capital. If the process restarts on the same day, the stored allocation is reused.

## Risk scales with the account

Live risk controls are percentages of each model's frozen allocation:

- `RISK_PER_TRADE_PCT=0.005` → 0.50% model risk budget per trade
- `MAX_POSITION_PCT=0.25` → maximum position notional is 25% of model allocation
- `DAILY_LOSS_PCT=0.02` → 2% model daily stop
- `MAX_ACCOUNT_EXPOSURE_PCT=0.50` → combined experiment gross-exposure ceiling
- configurable maximum trades/day and consecutive-loss cutoff

A trade is also capped by actual Alpaca cash and remaining global exposure room.

## Trader-style controls

The top bar is designed around safe experiment operation:

- **Start Session** — freezes the day's account equity and 50/50 model allocations, then starts both models.
- **Pause Entries** — blocks new entries while continuing to manage existing positions and exits.
- **Resume** — re-enables entries.
- **Drain & Stop** — takes no new positions and keeps managing existing positions until the experiment is flat, then stops.
- **Emergency Flatten** — cancels open orders and submits exits for PAPER positions immediately.

The normal Stop action intentionally does not abandon a live position.

## What the workstation shows

All pages use the same frozen session baseline and risk ledger.

**Home**
- live Alpaca account equity
- frozen session start equity
- account session P&L
- Model A / Model B allocations
- gross exposure and exposure limit
- entry-window state
- engine/cycle health
- both model dashboards side by side

**Model A / Model B**
- allocation and model equity
- realized + unrealized P&L
- session return and drawdown
- closed trades and win rate
- risk/trade, max position, daily stop, remaining loss room
- position monitor with entry/current/stop/exposure/unrealized P&L
- scanner state
- newest-first console activity feed

**Trades**
- actual order/fill ledger
- stable trade IDs linking entries and exits
- realized P&L from actual fills
- win rate, average trade, best/worst trade, and profit factor by model

## Restart protection

A daily session snapshot is written under `trade-data/`. The engine can recover attributable Model A/B positions from the journal after a process restart. If it sees a position that cannot be safely attributed to the experiment, Start Session is blocked instead of guessing.

## Validate before running

```bash
git pull
source .venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=src python validate.py
```

The validator is order-free. It checks:

- module imports
- paper-only safety lock
- dynamic 50/50 allocation
- percentage-scaled risk sizing
- Model A signal pipeline
- Model B momentum/catalyst pipeline
- actual-fill P&L
- complete pytest suite

Then verify your local Alpaca connection:

```bash
PYTHONPATH=src python validate.py --alpaca
```

The Alpaca validation reads the account and market data only. It does **not** submit, cancel, or close orders.

A healthy result ends with:

```text
Failed: 0
RESULT: READY
```

## Run

```bash
PYTHONPATH=src python run.py
```

Open `http://127.0.0.1:8000`.

## Important limitations

- Model allocations are virtual ledgers inside one Alpaca paper account.
- The current engine is polling-based; a full market/order WebSocket execution layer is still a future upgrade.
- Model B's RVOL remains a short-term bar measure; same-time-of-day multi-session RVOL is still planned.
- Float filtering and full Level 2/order-book confirmation require a separate reliable provider.
- Stops are application-managed today, so the process must remain available while positions are open.
- Paper fills do not reproduce all live-market slippage, queue position, spread, latency, halts, or market impact.

This is experimental software, not investment advice.
