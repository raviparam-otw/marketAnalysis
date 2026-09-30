# Alpaca Three-Model Intraday Lab

A local, **paper-trading-only** A/B/C intraday research workstation running three independent decision styles against the same market session.

- **Model A — CONTROL:** Opening Range / VWAP.
- **Model B — CHALLENGER:** aggressive Momentum Catalyst action-day strategy.
- **Model C — ADAPTIVE:** cost-controlled LLM multi-agent desk (technical + sentiment + bull/bear + risk + portfolio manager).

The application is intentionally locked to Alpaca paper trading.

## Session capital: automatic three-way split

The live engine no longer assumes a $100,000 account.

When **Start Session** is pressed, the engine reads the actual Alpaca paper account equity and freezes equal A/B/C allocations for that trading day.

- $100,000 equity → A $33,333.33 / B $33,333.33 / C $33,333.34
- $83,421.17 equity → A $27,807.06 / B $27,807.06 / C $27,807.05

The allocation is frozen for the trading day so one model's P&L cannot silently change another model's starting capital. Legacy same-day A/B sessions remain readable and are not retroactively re-split.

## Risk scales with the account

Live risk controls scale from each model's frozen allocation. Model A stays controlled, Model B uses the aggressive PAPER action-day profile, and Model C uses its own adaptive risk profile. Combined action-day gross exposure is capped at 80% of account starting equity by default. All three remain subject to cash, per-model exposure, open-position, trade-count, consecutive-loss, and daily-loss controls.

A trade is also capped by actual Alpaca cash and remaining global exposure room.

## Model C architecture

Model C is a real PAPER execution participant when an LLM endpoint is configured. It does **not** call the LLM for every stock every 10 seconds. A deterministic activity pre-filter ranks the universe first, then only the top shortlist is sent to the LLM once per configured decision interval.

The LLM workflow uses two cost-controlled calls inspired by the TradingAgents research design:

1. **Analyst committee** — technical view, financial-news/sentiment view, bull case, bear case, and risk flags for the shortlist.
2. **Portfolio/risk manager** — chooses at most one long BUY or HOLD, with confidence, rationale, invalidation, and stop guidance.

The result must refer to a symbol from the supplied shortlist and must exceed `MODEL_C_MIN_CONFIDENCE` before the engine can place a PAPER order. The engine, not the LLM, owns sizing, exposure limits, actual-fill stop rebasing, exits, and emergency flattening.

Model C accepts any OpenAI-compatible endpoint serving an open-source model. Examples include local Ollama/vLLM or the Hugging Face OpenAI-compatible router. For a finance-tuned stack, FinGPT models can be self-hosted behind the same interface. The Trading-R1 checkpoint is not assumed because its public model release is not currently available.

## Trader-style controls

The top bar is designed around safe experiment operation:

- **Start Session** — freezes the day's account equity and equal A/B/C model allocations, then starts both models.
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
- Model A / Model B / Model C allocations
- gross exposure and exposure limit
- entry-window state
- engine/cycle health
- both model dashboards side by side

**Model A / Model B / Model C**
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

A daily session snapshot is written under `trade-data/`. The engine can recover attributable Model A/B/C positions from the journal after a process restart. If it sees a position that cannot be safely attributed to the experiment, Start Session is blocked instead of guessing.

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
- dynamic three-way allocation
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
- Model C requires a configured OpenAI-compatible open-source LLM endpoint before it can trade.
- Model B's RVOL remains a short-term bar measure; same-time-of-day multi-session RVOL is still planned.
- Float filtering and full Level 2/order-book confirmation require a separate reliable provider.
- Stops are application-managed today, so the process must remain available while positions are open.
- Paper fills do not reproduce all live-market slippage, queue position, spread, latency, halts, or market impact.

This is experimental software, not investment advice.
