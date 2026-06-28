# AGENTS.md

## Verification

No tests, lint, or typecheck. Only:
```bash
python -m py_compile <file>
```
Or use the automated script:
```bash
bash verify.sh              # auto-detect changes via git diff
bash verify.sh --all        # rebuild all services
bash verify.sh --service X  # rebuild specific service
```

## Architecture

25 Docker microservices communicating via Redis streams. Runs in **mock mode** (`MOCK_MODE=true`) or **real mode** with OKX testnet (`MOCK_MODE=false`, `EXCHANGE=okx`, OKX API keys in `.env`). Synthetic OHLCV via `services/market-scanner/mock_scanner.py`; real OHLCV via `services/market-scanner-okx/scanner.py`.

**Data flow:** market-scanner → Redis streams (`market:data`, `market:indicators`) → strategy agents → `strategy:signals` → risk-manager → `risk:approved` → paper-trading → `trade:results`

**Core infra:** Redis (streams + key-value), TimescaleDB (hypertables), Ollama (gemma3:4b primary, qwen2.5:3b fallback).

## Docker profiles

- **Active by default:** redis, timescaledb, market-scanner, market-scanner-okx, qwen-analyzer, strategy-{scalping,swing,arbitrage}, risk-manager, paper-trading, paper-trading-okx, paper-trading-okx-swap, dashboard, circuit-breaker, regime-detector, sentiment, backtesting, evolution-agent, monitoring, stop-loss (merged with tracker), orchestrator, watchdog
- **Opt-in:** `--profile nautilus` (via docker-compose.experimental.yml), `--profile swarm` (via docker-compose.experimental.yml)
- **Removed:** freqtrade, freqtrade-bridge, strategy-router, ab-promoter, training-export, pt-* variants, feedback (service-level), stop-loss-tracker (merged into stop-loss)

## Dockerfile patterns

Two patterns coexist. **Root context** (`context: .`): copies root `requirements.txt` + `shared/` + single service file. Most services use this. **Local context** (`context: ./services/xxx`): own `requirements.txt`. Used by: watchdog.

Root-context services set `WORKDIR /app`, copy `shared/` to `/app/shared/`, service file to `/app/service/`. Entry point always: `sys.path.insert(0, "/app")` then `from shared.xxx import ...`.

## shared/ package

`shared/__init__.py` eagerly imports `config`, `redis_client`, `db`, `models`. Any `from shared.xxx` triggers all shared deps (redis, asyncpg, dotenv, pydantic).

Key modules: `config.py` (Settings with env vars + Redis stream names), `models.py` (TechnicalIndicators, TradingSignal, OHLCVData, TradeOrder, TradeResult), `redis_client.py` (singleton, stream publish/read, get_json/set_json), `db.py` (asyncpg pool singleton), `indicators.py` (compute_indicators shared by market-scanner and market-scanner-okx), `strategy_base.py` (BaseStrategyAgent shared by all 3 strategy agents — ~70% code reuse, each agent now ~80 lines).

## Alpha Zoo

111 factors in `shared/alpha_zoo/zoo/{academic,alpha101}/`. Each file must have:
- `__alpha_meta__` dict (`id`, `theme`, `formula_latex`, `columns_required`, `min_warmup_bars`)
- `compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame` — panel keys: "close", "open", "high", "low", "volume"
- `formula_latex` must use raw string `r"..."` (avoids SyntaxWarning from `\_`)
- Registry auto-discovers via AST scanning of `shared.alpha_zoo.zoo.*` packages

## TimescaleDB

Schema in `db/init.sql`. Tables: `ohlcv`, `indicators`, `signals`, `trades`, `portfolio_snapshots`, `qwen_feedback` — all hypertables on `time`. Connect: `postgresql://trader:trader123@timescaledb:5432/trader`.

Add columns with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for backward compat.

## Git

- Identity: `Andrés RG <mandresrg93@gmail.com>`
- Remote: `git@github.com:AndresRG82/orquetadorcrypto.git` (SSH)
- Branch: `main`

## Conventions

- User-facing: Spanish. Code/comments: English.
- Docker Python: 3.12-slim (most)
- No `pyproject.toml` — deps in root `requirements.txt` only

## Experimental compose

Services in `docker-compose.experimental.yml` (opt-in via `--profile`):
- `nautilus-bridge` (`--profile nautilus`)
- `swarm-coordinator` (`--profile swarm`)

Uses `trader-net` as `external: true` to communicate with main compose.

## Progress

### Done
- **Fase 0**: slippage de cierre corregido (net_pnl resta slippage + funding), fees reales OKX, DB schema actualizado
- **Fase 1**: Servicios redundantes eliminados (freqtrade, bridge, router, promoter), stop-loss fusionado con tracker, AGENTS.md reescrito, verify.sh creado
- **Fase 1.6**: BaseStrategyAgent creado (~70% código compartido), 3 estrategias refactorizadas (scalping 73ln, swing 91ln, arbitrage 113ln)
- **Fase 2.1**: batch_id/signal_id trazabilidad, trace_signals.py verifica 100 signal_ids contra signals/trades/qwen_feedback
- **Fase 2.3**: Replay determinista (mock_scanner seed, ReplayClock, deterministic_id, REPLAY_MODE), replay.py record/replay
- **Fase 2.4**: Risk-manager validation (test_risk_manager.py, 6/7 tests), run_validacion.sh
- **Fase 3**: Redis keys `strategy:params:*` y `risk:params` eliminadas; train/test split en backtesting y evolution-agent, OOS validation con `EVO_OOS_SHARPE_MIN=0.3`, `apply_vectorbt_results` solo deploya params con OOS aprobado
- **Walk-forward scalping**: `run_walk_forward()` con 158 ventanas deslizantes (train=2000, test=500, step=500) para scalping 5m; 15m y demás estrategias mantienen 80/20 split

### In Progress
- *(none)*
