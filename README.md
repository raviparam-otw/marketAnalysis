# Alpaca Dual-Model Intraday Lab

A local, **paper-trading-only** A/B intraday experiment using Alpaca market data and paper orders.

## What changed in v0.2

The dashboard now runs two models in parallel from one Start button:

- **Model A — Opening Range / VWAP:** the original breakout strategy, preserved as the control.
- **Model B — Momentum Catalyst:** a more selective momentum strategy using gap/change, relative volume, a recent news catalyst, VWAP/HOD context and a controlled pullback/continuation trigger.

Each model is assigned **$50,000 virtual capital**. Position size is no longer "use all available cash"; it is calculated from the configured dollars at risk and the distance between entry and the technical stop, with a separate maximum position notional.

> Experimental software, not investment advice. The broker client is hard-coded to Alpaca paper trading. Do not treat paper performance as expected live performance.

## Safety / risk controls

- Alpaca client is hard-coded with `paper=True`.
- $50k virtual capital per model by default.
- Default risk budget of $250 per trade and $12,500 maximum position notional.
- Daily model loss limit, trade-count limit and consecutive-loss limit.
- Cross-model symbol lock: Model A and Model B will not intentionally open the same symbol at the same time.
- Positions are flattened by the global kill switch.
- Entries stop at 2:30 PM ET and open positions are exited by 3:50 PM ET.
- Trade journal stores model ID, setup context, requested size, fills and realized P&L.

## Dashboard

The UI uses a VS Code Light-inspired blue/white theme.

- `/` — side-by-side Model A / Model B comparison with live consoles.
- `/model/A` — full Model A view.
- `/model/B` — full Model B view.
- `/trades` — side-by-side order/fill history and realized P&L.

**Start both** starts both models together. Stop and Kill apply to the complete experiment.

## Model A

Model A keeps the original control logic: opening-range breakout + session VWAP + one-minute relative-volume confirmation + simple broad-market alignment.

## Model B

Model B currently requires a combination of:

- configured price range;
- meaningful opening gap or intraday change;
- elevated relative volume;
- recent Alpaca news catalyst (configurable);
- price above VWAP and near high-of-day; and
- a controlled five-bar pullback followed by continuation.

The news adapter reads Alpaca's news endpoint and caches recent symbol headlines. News is a filter/context input, not an automatic buy signal.

## Position sizing

For a planned entry and technical stop:

`shares = min(risk_per_trade / (entry - stop), max_position_notional / entry, model_capital / entry)`

This replaces the old behavior that could allocate the full $100,000 account to one trade.

## P&L journal fix

Realized P&L is recalculated from the **actual filled entry and exit prices** once both fills are available. This replaces the earlier approximate P&L based on the pre-exit unrealized value.

## Setup

```bash
git clone https://github.com/raviparam-otw/marketAnalysis.git
cd marketAnalysis
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Add only Alpaca **paper** credentials to `.env`, then:

```bash
pytest
PYTHONPATH=src python run.py
```

Open `http://127.0.0.1:8000`.

## Important limitations

- Both models share one Alpaca paper account. The engine prevents intentional same-symbol overlap, but Alpaca still reports account-level positions/cash globally.
- Free Alpaca market data may use IEX rather than the full consolidated feed.
- This version polls rather than using a full tick/order-update streaming architecture; streaming remains a next-step improvement.
- The Model B relative-volume measure is still short-term bar RVOL, not yet the final multi-day same-time-of-day RVOL baseline.
- Float filtering and full Level 2/order-book confirmation are not yet implemented because they need a separate reliable data source/provider.
- Stops are application-managed. If the machine sleeps, loses power, or loses connectivity, management pauses until reconnect.
