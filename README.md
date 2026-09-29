# Alpaca Dual-Model Intraday Lab

A local, **paper-trading-only** A/B intraday experiment using Alpaca market data and paper orders.

## Models

- **Model A — Opening Range / VWAP:** original breakout strategy retained as the control.
- **Model B — Momentum Catalyst:** gap/change, relative volume, recent news catalyst, VWAP/HOD context, and pullback/continuation logic.

Each model has **$50,000 virtual capital** by default. Position size is calculated from dollars at risk and technical-stop distance instead of allocating the full paper account.

> Experimental software, not investment advice. The broker client is hard-coded to Alpaca paper trading.

## Validate locally before starting the engine

After pulling the repo and activating the virtual environment:

```bash
git pull
source .venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=src python validate.py
```

That command is **offline/order-free**. It checks module imports, paper-safety configuration, risk sizing, both strategy pipelines, actual-fill P&L journaling, and then runs the full pytest suite.

To also verify your local Alpaca paper credentials and **read-only** market-data connection:

```bash
PYTHONPATH=src python validate.py --alpaca
```

The Alpaca validation reads the account and SPY market data only. It does **not** submit, cancel, or close any order.

A healthy result ends with:

```text
Passed: <n>   Failed: 0
RESULT: READY
```

If anything fails, send the complete validation output back for diagnosis.

You can also run only the unit suite:

```bash
PYTHONPATH=src pytest -q
```

## Run the dashboard

```bash
PYTHONPATH=src python run.py
```

Open `http://127.0.0.1:8000`.

The UI uses a VS Code Light-inspired blue/white theme:

- `/` — side-by-side Model A / Model B dashboard and live consoles
- `/model/A` — Model A detail
- `/model/B` — Model B detail
- `/trades` — side-by-side trade/fill history

**Start both** starts both models together.

## Risk controls

- hard-coded Alpaca `paper=True`
- $50k virtual capital per model
- configurable dollars-at-risk per trade
- maximum position notional
- daily model loss limit
- max trades/day and consecutive-loss limit
- cross-model symbol lock
- kill switch for the full paper experiment
- no new entries after 2:30 PM ET
- end-of-day exits by 3:50 PM ET

## Trade journal

The dated JSON journal records model ID, signal/setup context, requested quantity, actual fills, exit reason, and realized P&L. Realized P&L is calculated from the **actual entry and exit fill prices** after both orders fill.

## Important limitations

- Both models share one Alpaca paper account; the $50k allocations are virtual ledgers.
- Free Alpaca data may use IEX rather than the complete consolidated feed.
- Model B's RVOL is still a short-term bar measure; multi-day same-time-of-day RVOL is a future enhancement.
- Float filtering and full Level 2 need a separate reliable provider and are not implemented yet.
- The engine currently polls; full streaming execution/order updates remain a future enhancement.
- Stops are currently application-managed, so the process must remain running.
