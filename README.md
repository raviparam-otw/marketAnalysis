# Alpaca A/C Intraday Lab

A local, **paper-trading-only** A/C intraday research workstation running two independent decision styles against the same market session.

- **Model A — CONTROL:** Opening Range / VWAP across the dynamic universe, with multi-position support and faster failed-breakout exits.
- **Model C — ADAPTIVE:** Fin-R1 financial reasoner + Kronos K-line forecaster + FinBERT financial sentiment, orchestrated as a cost-controlled multi-agent desk.

The application is intentionally locked to Alpaca paper trading.

## Session capital: automatic A/C split

The live engine no longer assumes a $100,000 account.

When **Start Session** is pressed, the engine reads the actual Alpaca paper account equity and freezes equal A/C allocations for that trading day.

- $100,000 equity → A $50,000 / C $50,000
- $83,421.17 equity → A $41,710.59 / C $41,710.58

The allocation is frozen for the trading day so one model's P&L cannot silently change another model's starting capital. Legacy same-day sessions are migrated to A/C only when the paper account is flat.

## Risk scales with the account

Live risk controls scale from each model's frozen allocation. Model A stays controlled and Model C uses its adaptive risk profile. Combined gross exposure is capped at 80% of account starting equity by default. Both models remain subject to cash, per-model exposure, open-position, trade-count, consecutive-loss, and daily-loss controls.

A trade is also capped by actual Alpaca cash and remaining global exposure room.

## Model C architecture

Model C is now a finance-native three-model intelligence stack:

1. **FinBERT — financial sentiment.** The supplied company/news headline is scored as positive, negative, or neutral before it reaches the reasoning layer.
2. **Kronos-small — K-line forecast.** The candidate's recent OHLCV bars are passed through Kronos to produce a short-horizon direction, predicted close, upside and downside summary.
3. **Fin-R1 — financial reasoner.** Fin-R1 receives the deterministic market features plus FinBERT sentiment and Kronos forecast. It runs the analyst committee and portfolio/risk-manager reasoning, then returns at most one BUY or HOLD decision.

Model C still does **not** call heavy models for the entire market universe. The deterministic activity scanner first reduces the universe to the top shortlist. FinBERT and Kronos run only on that shortlist, and Fin-R1 is called once per configured decision interval.

The execution path is therefore:

```text
Market universe
  -> deterministic activity filter
  -> shortlist
  -> FinBERT sentiment
  -> Kronos K-line forecast
  -> Fin-R1 analyst committee
  -> Fin-R1 portfolio/risk manager
  -> confidence gate
  -> deterministic position sizing/risk engine
  -> Alpaca PAPER order
```

The LLM cannot invent a ticker: a Fin-R1 BUY is accepted only when its symbol exists in the engine-generated shortlist. It must also exceed `MODEL_C_MIN_CONFIDENCE`. Position sizing, model/account exposure, actual-fill risk rebasing, exit management, daily loss limits, and emergency flattening remain deterministic and outside the LLM.

Fin-R1 is the primary Model C decision engine. FinBERT and Kronos enrich its evidence when available; if either auxiliary component is unavailable, Model C logs the degradation and continues with Fin-R1.

### One-command local setup + test

If you want the entire local Model C setup handled automatically, run:

```bash
git pull
source .venv/bin/activate
bash scripts/auto_model_c_macos.sh
```

That single command will:

- reuse existing Fin-R1/Kronos/FinBERT downloads when already present;
- install/download anything missing;
- start the local Fin-R1 server if it is not already running;
- detect the exact model ID exposed by the server;
- update only the `MODEL_C_*` values in your existing `.env` while preserving Alpaca credentials;
- run the full FinBERT + Kronos + Fin-R1 stack check;
- run `sample_test.py --live-llm`.

The validation path does not submit Alpaca orders.

### Install Model C on Apple Silicon

Model weights are intentionally excluded from Git. Run:

```bash
git pull
source .venv/bin/activate
bash scripts/setup_model_c_macos.sh
```

The setup script:

- installs the separate Model C dependencies;
- converts the official `SUFE-AIFLM-Lab/Fin-R1` weights to a local 4-bit MLX checkpoint under `.models/Fin-R1-4bit`;
- clones the official Kronos implementation under `.models/Kronos`;
- caches `NeoQuasar/Kronos-small` and `NeoQuasar/Kronos-Tokenizer-base`;
- caches `ProsusAI/finbert`.

The `.models/` directory is git-ignored.

Start Fin-R1 in a separate terminal:

```bash
source .venv/bin/activate
bash scripts/start_finr1_macos.sh
```

The local Fin-R1 server listens on `http://127.0.0.1:8080/v1`.

The launcher uses the repository's **single-threaded MLX server wrapper** rather than `mlx_lm.server`. This avoids worker-thread/Metal stream failures that can cause an empty HTTP response on some Apple Silicon/Python 3.13 combinations.

Then verify the complete finance stack without placing any Alpaca orders:

```bash
source .venv/bin/activate
PYTHONPATH=src python scripts/check_model_c_stack.py
```

A successful run ends with:

```text
[PASS] FinBERT sentiment
[PASS] Kronos forecasts
[PASS] Fin-R1 reasoning endpoint
RESULT: MODEL C FULL STACK READY
```

## Trader-style controls

The top bar is designed around safe experiment operation:

- **Start Session** — freezes the day's account equity and equal A/C model allocations, then starts both models.
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
- Model A / Model C allocations
- gross exposure and exposure limit
- entry-window state
- engine/cycle health
- both model dashboards side by side

**Model A / Model C**
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

A daily session snapshot is written under `trade-data/`. The engine can recover attributable Model A/C positions from the journal after a process restart. If it sees a position that cannot be safely attributed to the experiment, Start Session is blocked instead of guessing.

## Local sample-data test

A checked-in synthetic fixture lets you exercise **Models A and C locally without Alpaca connectivity or order submission**.

Files:

- `sample-data/market-bars.csv` — deterministic 1-minute bars used by Model A and Model C, plus SPY and QQQ.
- `sample-data/news.json` — synthetic catalysts for the momentum and LLM paths.
- `sample-data/model-c-responses.json` — deterministic Fin-R1 analyst-committee and portfolio-manager responses.
- `sample-data/model-c-intelligence.json` — deterministic FinBERT sentiment and Kronos forecast evidence.
- `sample_test.py` — loads the fixture, runs A/C, applies each model's risk sizing, and submits **no orders**.

Run the complete offline sample:

```bash
git pull
source .venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=src python sample_test.py
```

Expected ending:

```text
[PASS] Model A
[PASS] Model C
RESULT: READY
```

To run the exact same sample through the **real local Model C stack** instead of fixtures:

```bash
PYTHONPATH=src python sample_test.py --live-llm
```

In this mode the sample uses real FinBERT sentiment, real Kronos forecasts, and the local Fin-R1 endpoint. It still does not connect to Alpaca or submit orders.

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
- dynamic A/C allocation
- percentage-scaled risk sizing
- Model A signal pipeline
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
- Model C requires the local Fin-R1 endpoint plus FinBERT and Kronos when full-stack mode is enabled.
- Float filtering and full Level 2/order-book confirmation require a separate reliable provider.
- Stops are application-managed today, so the process must remain available while positions are open.
- Paper fills do not reproduce all live-market slippage, queue position, spread, latency, halts, or market impact.

This is experimental software, not investment advice.
