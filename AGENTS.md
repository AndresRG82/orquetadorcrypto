# AGENTS.md

## Verification

No tests, lint, or typecheck. Only verification:
```bash
python -m py_compile <file>
```

## Architecture

40+ Docker microservices communicating via Redis streams. Runs in **mock mode** (`MOCK_MODE=true`) or **real mode** with OKX testnet (`MOCK_MODE=false`, `EXCHANGE=okx`, OKX API keys in `.env`). Synthetic OHLCV via `services/market-scanner/mock_scanner.py`; real OHLCV via `services/market-scanner-okx/scanner.py`

**Data flow:** market-scanner (mock, optional OKX real) → Redis streams (`market:data`, `market:indicators`) → strategy agents → `strategy:signals` → risk-manager → `risk:approved` → paper-trading → `trade:results`

**Core infra:** Redis (streams + key-value), TimescaleDB (hypertables), Ollama (gemma3:4b primary, qwen2.5:3b fallback).

## Docker profiles

- **Active by default:** redis, timescaledb, market-scanner, market-scanner-okx, qwen-analyzer, strategy-{scalping,swing,arbitrage}, risk-manager, paper-trading, paper-trading-okx, paper-trading-okx-swap, dashboard, circuit-breaker, regime-detector, sentiment, backtesting, evolution-agent, monitoring, stop-loss, stop-loss-tracker, orchestrator
- **Disabled** (`profiles: ["disabled"]`): 10 paper-trading A/B variants (`pt-*`), 3 freqtrade containers, freqtrade-bridge
- **Opt-in:** `--profile nautilus` (nautilus-bridge), `--profile swarm` (swarm-coordinator)

## Dockerfile patterns

Two patterns coexist. **Root context** (`context: .`): copies root `requirements.txt` + `shared/` + single service file. Most services use this. **Local context** (`context: ./services/xxx`): own `requirements.txt`. Used by: watchdog, freqtrade-bridge, nautilus-bridge, swarm-coordinator.

Root-context services set `WORKDIR /app`, copy `shared/` to `/app/shared/`, service file to `/app/service/`. Entry point always: `sys.path.insert(0, "/app")` then `from shared.xxx import ...`.

## shared/ package

`shared/__init__.py` eagerly imports `config`, `redis_client`, `db`, `models`. Any `from shared.xxx` triggers all shared deps (redis, asyncpg, dotenv, pydantic). Install locally:
```bash
pip install --break-system-packages python-dotenv pydantic pandas numpy redis asyncpg scipy
```

Key modules: `config.py` (Settings with env vars + Redis stream names), `models.py` (TechnicalIndicators, TradingSignal, OHLCVData), `redis_client.py` (singleton, stream publish/read, get_json/set_json), `db.py` (asyncpg pool singleton), `indicators.py` (compute_indicators shared by market-scanner and market-scanner-okx).

## Alpha Zoo

111 factors in `shared/alpha_zoo/zoo/{academic,alpha101}/`. Each file must have:
- `__alpha_meta__` dict (`id`, `theme`, `formula_latex`, `columns_required`, `min_warmup_bars`)
- `compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame` — panel keys: "close", "open", "high", "low", "volume"
- `formula_latex` must use raw string `r"..."` (avoids SyntaxWarning from `\_`)
- Registry auto-discovers via AST scanning of `shared.alpha_zoo.zoo.*` packages

## TimescaleDB

Schema in `db/init.sql`. Tables: `ohlcv`, `indicators`, `signals`, `trades`, `portfolio_snapshots`, `qwen_feedback` — all hypertables on `time`. Connect: `postgresql://trader:trader123@timescaledb:5432/trader`.

## Git

- Identity: `Andrés RG <mandresrg93@gmail.com>`
- Remote: `git@github.com:AndresRG82/orquetadorcrypto.git` (SSH)
- Branch: `main`

## Conventions

- User-facing: Spanish. Code/comments: English.
- Docker Python: 3.12-slim (most), 3.11-slim (nautilus/swarm/freqtrade-bridge)
- No `pyproject.toml` — deps in root `requirements.txt` only
