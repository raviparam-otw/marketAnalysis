# Alpaca Intraday Lab

A local, **paper-trading-only** intraday experiment for Alpaca. The live engine discovers all active Alpaca US equities, screens them for tradeability and liquidity, and submits a long paper order only when an opening-range breakout is confirmed by VWAP, relative volume, and broad-market direction.

> This is experimental software, not investment advice. It intentionally cannot connect to Alpaca live trading. Paper results can differ materially from real execution.

## Safety controls

- Hard-coded `paper=True` broker client plus configuration validation
- $100,000 maximum exposure even if Alpaca displays greater paper buying power
- One open position and at most one losing trade per day
- 2.5% stop, 5% target, and trailing-stop logic
- $5,000 daily loss limit
- Experiment stops at $50,000 or $150,000 equity
- No entries after 2:30 PM ET; positions close by 3:50 PM ET
- Localhost-only dashboard with stop and kill-switch controls
- `.env` and credentials excluded from Git

## Strategy

After the first 15 minutes of the regular session, the engine looks for:

1. price above the 9:30–9:44 ET opening-range high;
2. price above session VWAP;
3. current one-minute volume at least 1.5× the prior 20-bar average; and
4. SPY or QQQ rising on the latest bar.

If multiple symbols qualify, the engine selects the greatest relative-volume candidate. A signal is not a guarantee of profit.

## Dynamic symbol universe

The live engine no longer relies on the ten-symbol `WATCHLIST`. Every 30 minutes it:

1. requests all active US equities from Alpaca's Assets API;
2. removes OTC, non-tradable, non-fractional, explicitly excluded, warrant, unit, right, preferred, leveraged, and inverse instruments;
3. requests IEX snapshots in batches and removes symbols below $5, below $1 million in daily dollar volume, or above a 0.5% bid/ask spread; and
4. ranks the survivors by dollar volume and fetches minute bars for the top 100 in small batches so Alpaca's combined response limit does not silently omit symbols.

These values are configurable in `.env`. `WATCHLIST` remains the historical-backtest universe and the safe live fallback if a universe refresh temporarily returns no candidates. The current Assets API is not a point-in-time historical universe, so applying it directly to old backtests would create survivorship bias.

## Setup (macOS, Python 3.13)

```bash
git clone https://github.com/raviparam-otw/marketAnalysis.git
cd marketAnalysis
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
```

Edit `.env` and insert **paper** credentials from the Alpaca Paper Trading dashboard. Never paste credentials into chat or commit `.env`.

## Verify

```bash
pytest
```

## Backtest first

The backtester uses Alpaca's free IEX minute bars, applies conservative slippage, permits one long position at a time, and writes a detailed JSON trade log:

```bash
PYTHONPATH=src python backtest.py --start 2026-07-01 --end 2026-09-01
```

Do not start the paper engine merely because one backtest is profitable. Repeat across rising, falling, volatile, and sideways periods, and reserve a later period for out-of-sample testing.

## Run

```bash
source .venv/bin/activate
PYTHONPATH=src python run.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The engine starts only when you click **Start engine**. **Kill switch** cancels open orders, requests closure of every paper position, and stops scanning.\n\n## Daily trade journal\n\nWhen the paper engine starts it creates `trade-data/trades-YYYY-MM-DD.json` (configurable with `TRADE_LOG_DIR`). Every buy and sell order is written to that file immediately after submission using an atomic file replacement, so the day does not depend on a graceful 4:00 PM shutdown. The journal keeps order ID, symbol, side, reason, requested size, signal price, status, fill quantity/price/time when available, approximate exit P&L, and a running daily summary. Pending Alpaca orders are refreshed on later engine cycles so accepted orders can become filled/canceled/rejected in the same JSON file. The `trade-data/` directory is intentionally excluded from Git.

## Important limitations

- Free Alpaca paper accounts use IEX data, which is not the full consolidated market feed.
- Stops are monitored by this local process. If the Mac sleeps, loses power, or loses connectivity, the process cannot send an exit until it reconnects.
- Paper fills do not reproduce all real-world spread, latency, partial-fill, or market-impact behavior.
- The relative-volume calculation uses recent intraday bars, not a multi-day time-of-day baseline.
- Version 1 is long-only and does not trade options, crypto, or leveraged positions.

Keep the Mac awake and monitor the dashboard whenever the engine is running.
