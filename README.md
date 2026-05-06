# Asset Universe AI

Autonomous AI investing agents for the [Asset Universe](https://asset-universe.vercel.app)
education platform. Three agents (short-, mid-, long-term horizon) trade simulated
capital using textbook quant strategies, review their own performance on schedule,
and adapt their strategy parameters via Anthropic's Claude API.

## Architecture

```
[ Next.js frontend ]  ─►  [ FastAPI ]  ─►  [ SQLite ]
                              │
                              ├── APScheduler (trade + review cadences)
                              ├── Claude API (self-review)
                              └── Finnhub / CoinGecko / Alpha Vantage
```

## The three agents

| Agent      | Starting strategy   | Trade cadence              | Review cadence | Universe                              |
|------------|---------------------|----------------------------|----------------|---------------------------------------|
| short_term | Momentum            | Every 30 min, market hours | Weekly         | Stocks + ETFs                         |
| mid_term   | Trend following     | Daily, after close         | Monthly        | Stocks + ETFs + commodity ETFs        |
| long_term  | Risk parity         | Weekly, Friday close       | Quarterly      | ETFs + commodity ETFs + crypto        |

Plus an emergency review trigger if any agent's drawdown breaches 15%.

## How the self-review works

1. **Performance summary** built from the last N snapshots: total return, max drawdown,
   Sharpe, recent trades.
2. **Claude prompt** sent with current strategy params + performance + available templates.
3. **Structured JSON response** parsed and validated:
   - `action` ∈ {`keep`, `tune`, `switch`, `blend`}
   - `new_params` must change each parameter by ≤ 30%
   - `new_template` must be in the registered library
4. **Validated changes applied**, logged in `strategy_history` and `decisions`.
5. **Rejected proposals** also logged with rejection reason.

The AI never writes new code. It picks from the strategy registry and tunes
parameters within guardrails. Every change is auditable.

## Strategy registry (v1)

- **momentum** — cross-sectional, top-N by lookback return (Jegadeesh-Titman 1993)
- **trend_following** — moving-average crossover, vol-targeted sizing
- **risk_parity** — inverse-vol weighting with asset-class caps

Add new templates in `app/strategies/` and register in `app/strategies/__init__.py`.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # fill in your API keys
python -m app.bootstrap    # creates the three agents
```

## Running

```bash
# Single cycle (testing)
python -m app.run_once trade short_term
python -m app.run_once review mid_term

# Production: scheduler runs inside the FastAPI process
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Deployment

Designed for [Railway](https://railway.app) or [Render](https://render.com):

1. Connect this repo
2. Set environment variables from `.env.example`
3. Mount a persistent volume at `/data` and set
   `DATABASE_URL=sqlite:////data/asset_universe_ai.db`
4. Set the start command to `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

## Public API

| Endpoint                              | What it returns                          |
|---------------------------------------|------------------------------------------|
| `GET /api/agents`                     | Card-summary for all three agents        |
| `GET /api/agents/{id}/portfolio`      | Cash + holdings for one agent            |
| `GET /api/agents/{id}/trades`         | Recent trades                            |
| `GET /api/agents/{id}/performance`    | Time-series + summary stats              |
| `GET /api/agents/{id}/strategy`       | Current template, params, plain-English  |
| `GET /api/agents/{id}/decisions`      | Self-review log with reasoning           |

The Next.js frontend hits these to render the AI Investors section.
