# Crypto Trader - Sistema Completo

## Arquitectura General

Sistema de trading automatizado con 40+ contenedores Docker, Qwen LLM para análisis de señales, y 20 instancias de paper trading para A/B testing.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         INFRASTRUCTURE                                      │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Host: CachyOS, NVIDIA GTX 1660 SUPER (6GB VRAM)                          │
│  Ollama: gemma3:4b (primary), qwen2.5:3b (fallback)                       │
│  Constraint: num_parallel=1, num_predict=96                               │
│                                                                             │
│  Docker Containers:                                                         │
│  ────────────────────────────────────────────────────────────────────────  │
│  Core:     market-scanner, qwen-analyzer, risk-manager, orchestrator       │
│  Strategy: strategy-scalping, strategy-swing, strategy-arbitrage           │
│  Support:  stop-loss, stop-loss-tracker, feedback, sentiment, backtesting  │
│  Infra:    watchdog, monitoring, training-export, evolution-agent          │
│  Safety:   circuit-breaker, regime-detector, ab-promoter                  │
│  Data:     redis, timescaledb, grafana, dashboard                          │
│  Trading:  20 paper-trading instances (A/B test)                           │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

```
Market Scanner (CCXT)
    │
    ├─▶ market:data (Redis Stream)
    │   market:indicators (Redis Stream)
    │
    ▼
Qwen Analyzer (gemma3:4b)
    │
    ├─▶ strategy:signals (Redis Stream)
    │
    ▼
Strategy Router (Kelly Criterion)
    │
    ├─▶ strategy:scalping (1m/5m/15m)
    ├─▶ strategy:swing (1h/4h/1d)
    └─▶ strategy:arbitrage (cross-exchange)
            │
            ▼
        Risk Manager
            │
            ├─ Circuit Breaker Check
            ├─ Dynamic Symbol Exclusion
            ├─ Drawdown Check
            ├─ Correlation Check
            ├─ Cooldown Check
            ├─ Confidence Check
            ├─ Cash Check
            └─ Risk/Reward Check
                    │
                    ▼
            Paper Trading (20 instances)
                │
                ├─▶ trade:orders (Redis Stream)
                ├─▶ trade:results (Redis Stream)
                │
                ▼
            Stop-Loss Monitor
                │
                ├─▶ ATR-based trailing stops
                │
                ▼
            Stop-Loss Tracker
                │
                ├─▶ 30min monitoring
                ├─▶ would_have_been_profitable
                │
                ▼
            Feedback Loop
                │
                ├─▶ Analyzes results
                ├─▶ Adjusts params
                │
                ▼
            Evolution Agent (every 6h)
                │
                ├─▶ Auto-optimizes
                ├─▶ Dynamic Symbol Exclusion
                ├─▶ Auto-schedule Tuning
                └─▶ Rollback if >5% PnL drop or >10% WR drop
                    │
                    ▼
                Training Export
                    │
                    ├─▶ stop-loss tracker → JSONL
                    └─▶ Fine-tuning pipeline
```

## Safety Systems

### Circuit Breaker
- **Docker:** `crypto-trader-circuit-breaker-1`
- **Function:** Stops the system automatically on destructive patterns
- **Checks:**
  - Consecutive losses ≥ 7 → pause 30min
  - Loss rate > 70% in 60min window → pause 30min
  - Drawdown > 3% in 15min → pause 120min (critical)
  - Signal spike > 10x average → pause 30min
- **Redis:** `circuit:state`, `circuit:history`, `alerts:critical`

### Regime Detector
- **Docker:** `crypto-trader-regime-detector-1`
- **Function:** Classifies market regime for strategy filtering
- **Regimes:** trending_up, trending_down, ranging, volatile
- **Strategy Map:**
  - trending_up → swing
  - trending_down → swing, scalping
  - ranging → scalping
  - volatile → block all
- **Redis:** `market:regime`

### A/B Auto-promoter
- **Docker:** `crypto-trader-ab-promoter-1`
- **Function:** Promotes winning instances, deprecates losers
- **Rules:**
  - Min trades: 20
  - Min WR: 40%
  - Deprecation WR: <35%
  - Capital boost: +20% to winner
  - Capital penalty: -10% to loser
- **Redis:** `ab:promotions`

### Dynamic Symbol Exclusion
- **Integrated in:** evolution-agent
- **Function:** Auto-excludes symbols with sustained losses
- **Rules:**
  - Eval window: 3 days
  - Min trades: 5
  - PnL threshold: -$15
  - Exclusion period: 48h
- **Redis:** `risk:excluded_symbols`

### Auto-schedule Tuning
- **Integrated in:** evolution-agent
- **Function:** Recalculates optimal trading hours weekly
- **Redis:** `time_filter:schedule`

### LLM Graceful Degradation
- **Integrated in:** qwen-analyzer
- **Function:** Reduces frequency when GPU is saturated
- **Levels:**
  - normal: GPU < 70%, batch=3, sleep=1s
  - reduced: GPU 70-85%, batch=2, sleep=5s
  - minimal: GPU > 85%, batch=1, sleep=15s
- **Redis:** `llm:degradation_level`

## Data Layer

### Redis (Cache + Streams)

**Streams:**
- `market:data` - OHLCV data from market scanner
- `market:indicators` - Technical indicators (RSI, MACD, BB, ATR, etc.)
- `strategy:signals` - Trading signals from strategies
- `risk:approved` - Risk-approved signals
- `trade:orders` - Paper trading orders
- `trade:results` - Trade results (PnL, fees, etc.)
- `alerts:critical` - Real-time alerts
- `ab:promotions` - A/B promotion history

**Keys:**
- `portfolio:state` - Main portfolio state
- `paper_trading:{instance}` - Instance-specific state
- `portfolio:stats` - Main portfolio stats
- `portfolio:stats:{instance}` - Instance-specific stats
- `strategy:params:{name}` - Strategy parameters
- `strategy:config:{name}` - Strategy configuration
- `risk:params` - Risk manager parameters
- `risk:excluded_symbols` - Dynamically excluded symbols
- `sentiment:current` - Fear & Greed index
- `stop_loss_tracker:tracked` - Tracked stopped-out signals
- `watchdog:status` - Container health status
- `evolution:*` - Evolution agent data
- `circuit:state` - Circuit breaker state
- `circuit:history` - Circuit breaker history
- `market:regime` - Current market regime
- `llm:degradation_level` - GPU load level
- `time_filter:schedule` - Auto-calculated trading hours
- `training:export_stats` - Training export statistics

### TimescaleDB (Time Series)

**Tables:**
- `trades` - All executed trades
- `signals` - All evaluated signals
- `indicators` - Technical indicators
- `qwen_feedback` - LLM feedback data

### Grafana (Dashboards)

- Portfolio performance over time
- Signal quality metrics
- Strategy comparison
- System health

URL: http://localhost:3000

## Core Services

### Market Scanner

- **Docker:** `crypto-trader-market-scanner-1`
- **Function:** Fetches OHLCV data for 20 pairs × 7 timeframes
- **Pairs:** BTC, ETH, BNB, SOL, XRP, ADA, DOGE, AVAX, DOT, LINK, MATIC, UNI, SHIB, LTC, ATOM, NEAR, FTM, ARB, OP, SUI
- **Timeframes:** 1m, 5m, 15m, 1h, 4h, 1d, 1w
- **Indicators:** RSI, MACD, Bollinger Bands, ATR, EMA, SMA, Volume
- **Output:** `market:data`, `market:indicators` streams

### Qwen Analyzer

- **Docker:** `crypto-trader-qwen-analyzer-1`
- **Model:** gemma3:4b (primary), qwen2.5:3b (fallback)
- **Constraint:** 6GB VRAM, num_parallel=1
- **Function:** Analyzes indicators and generates trading signals
- **Output:** Structured JSON with schema enforcement
- **Output Stream:** `strategy:signals`
- **Features:**
  - Graceful degradation based on GPU load
  - Caching per timeframe
  - Feedback integration

### Risk Manager

- **Docker:** `crypto-trader-risk-manager-1`
- **Function:** Evaluates signals against risk rules
- **Checks:**
  - Circuit breaker status
  - Dynamic symbol exclusion
  - Max concurrent positions (10)
  - Max drawdown (10%)
  - Correlation (same coin)
  - Cooldowns (scalping: 15min, swing: 1h, arbitrage: 2h)
  - Confidence threshold
  - Cash availability
  - Risk/Reward ratio
  - Loss cooldown (3=15min, 5=30min, 7=60min)
  - High confidence bypass (≥90% = 50% position size)
- **Output:** `risk:approved` stream
- **DB:** Saves every signal to `signals` table

### Strategy Router

- **Docker:** `crypto-trader-strategy-router-1`
- **Function:** Routes signals to appropriate strategies
- **Algorithm:** Kelly Criterion
- **Auto-deactivate:** Strategies with <35% win rate

### Strategy Agents

#### Scalping
- **Docker:** `crypto-trader-strategy-scalping-1`
- **Timeframes:** 1m, 5m, 15m
- **SL:** 2.5 ATR
- **TP:** 3.5 ATR
- **R:R:** 1.40:1
- **Cooldown:** 1800s (30min)
- **Min Score:** 3
- **Excludes:** PEPE, BTC

#### Swing
- **Docker:** `crypto-trader-strategy-swing-1`
- **Timeframes:** 1h, 4h, 1d
- **SL:** 3.0 ATR
- **TP:** 4.0 ATR

#### Arbitrage
- **Docker:** `crypto-trader-strategy-arbitrage-1`
- **Function:** Cross-exchange price differences

### Stop-Loss Monitor

- **Docker:** `crypto-trader-stop-loss-1`
- **Function:** Monitors open positions for stop-loss/take-profit
- **Algorithm:** ATR-based trailing stops

### Stop-Loss Tracker

- **Docker:** `crypto-trader-stop-loss-tracker-1`
- **Function:** Monitors stopped-out signals for 30 minutes
- **Metrics:**
  - `would_have_been_profitable` - Did price move in predicted direction?
  - `max_favorable` - Maximum favorable move after stop
  - `max_adverse` - Maximum adverse move after stop

### Feedback Loop

- **Docker:** `crypto-trader-feedback-1`
- **Function:** Analyzes trade results and adjusts strategy parameters

### Sentiment Analyzer

- **Docker:** `crypto-trader-sentiment-1`
- **Function:** Fetches Fear & Greed index
- **Output:** `sentiment:current` key

### Backtesting Engine

- **Docker:** `crypto-trader-backtesting-1`
- **Library:** vectorbt 1.0.0
- **Function:** Historical strategy validation

### Training Export

- **Docker:** `crypto-trader-training-export-1`
- **Function:** Exports data for LLM fine-tuning
- **Sources:**
  - qwen_feedback table
  - closed trades
  - stop-loss tracker data
- **Output:** JSONL format for Ollama fine-tuning

### Evolution Agent

- **Docker:** `crypto-trader-evolution-agent-1`
- **Frequency:** Every 6 hours
- **Function:** Auto-optimizes strategy parameters
- **Features:**
  - Dynamic symbol exclusion
  - Auto-schedule tuning (recalculates optimal hours weekly)
  - Parameter clamping (max_position_pct ≤ 0.30, max_drawdown_pct ≤ 0.20)
- **Rollback:** If PnL drops >5% or win rate drops >10%

### Watchdog

- **Docker:** `crypto-trader-watchdog-1`
- **Function:** Monitors container health
- **Checks:** Consumer idle via XINFO CONSUMERS
- **Action:** Auto-restart with 60s timeout
- **Monitors:** market-scanner, qwen-analyzer, risk-manager, orchestrator, strategies

### Monitoring

- **Docker:** `crypto-trader-monitoring-1`
- **Function:**
  - Snapshots portfolio state every 6 hours
  - Real-time alerts every 5 minutes
- **Alerts:**
  - Circuit breaker trips
  - PnL drops below threshold
  - Open positions exceed limit
- **Output:** `/app/logs/monitoring/monitor_YYYY-MM-DD.jsonl`
- **Stream:** `alerts:critical`

## Paper Trading (A/B Test)

### Configuration

**Engine Parameters:**
- `PT_INSTANCE` - Instance identifier
- `PT_STATE_KEY` - Redis key for state
- `PT_STATS_KEY` - Redis key for stats
- `PT_STRATEGY_FILTER` - Comma-separated strategies (empty=all)
- `PT_MIN_CONFIDENCE` - Minimum confidence threshold
- `PT_MAX_TRADES_PER_DAY` - Daily trade limit (0=unlimited)
- `PT_TIMEFRAME_FILTER` - Allowed timeframes (e.g. "5m,15m")
- `PT_SENTIMENT_GATED` - "fear" or "greed" or empty=off
- `PT_SENTIMENT_THRESHOLD` - Fear below, greed above
- `PT_TIME_FILTER` - Allowed UTC hours (e.g. "5,6,8,17,22")

### A/B Test: Time Filter

**WITH Time Filter (5,6,8,17,22h UTC):**
- `pt-main-tf`
- `pt-conservative-tf`
- `pt-swing-tf`
- `pt-highconf-tf`
- `pt-multitf-tf`
- `pt-lowfreq-tf`
- `pt-sentiment-tf`
- `pt-aggressive` (original)
- `pt-scalping` (original)
- `pt-meanrev` (original)

**WITHOUT Time Filter (24/7):**
- `pt-main` (paper-trading)
- `pt-conservative`
- `pt-swing`
- `pt-highconf`
- `pt-multitf`
- `pt-lowfreq`
- `pt-sentiment`
- `pt-aggressive-notf`
- `pt-scalping-notf`
- `pt-meanrev-notf`

**Reason:** 11h UTC caused -$441.40 (death cycle)
**Optimal hours:** 5h (+$1.95), 6h (+$0.71), 8h (+$2.99), 17h (+$0.50), 22h (+$5.76)

### Instance Configurations

| Instance | Strategy | Confidence | Timeframe | Sentiment | Time Filter |
|----------|----------|------------|-----------|-----------|-------------|
| main | all | 0% | all | off | 24/7 |
| conservative | all | 70% | all | off | 24/7 |
| aggressive | all | 50% | all | off | 24/7 |
| scalping | scalping | 0% | all | off | 24/7 |
| swing | swing | 0% | all | off | 24/7 |
| highconf | all | 75% | all | off | 24/7 |
| multitf | all | 0% | 5m,15m,1h,4h | off | 24/7 |
| meanrev | scalping | 70% | all | off | 24/7 |
| lowfreq | all | 70% | all | off | 5/day |
| sentiment | all | 0% | all | fear | 24/7 |

## Dashboard (FastAPI)

**URL:** http://localhost:8001

### Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/portfolios` | 20 instances comparison |
| `GET /api/analytics/time-performance` | Hourly PnL breakdown |
| `GET /api/analytics/symbol-patterns` | Symbol × Strategy performance |
| `GET /api/analytics/win-streaks` | Win/Loss streaks |
| `GET /api/analytics/slippage` | Fees + Slippage analysis |
| `GET /api/stop-loss-tracker` | Stopped-out signals analysis |
| `GET /api/monitoring` | Portfolio snapshots (6h) |
| `GET /api/signals` | All evaluated signals |
| `GET /api/watchdog` | Container health |
| `GET /api/sentiment` | Fear & Greed index |
| `GET /api/backtest/{strategy}` | Backtesting results |
| `GET /api/evolution` | Evolution agent history |
| `GET /api/alerts` | Real-time alerts (last 50) |
| `GET /api/circuit` | Circuit breaker state + history |

### Analytics Examples

**Time Performance:**
```bash
curl http://localhost:8001/api/analytics/time-performance
```

**Symbol Patterns:**
```bash
curl http://localhost:8001/api/analytics/symbol-patterns
```

**Win Streaks:**
```bash
curl http://localhost:8001/api/analytics/win-streaks
```

**Slippage:**
```bash
curl http://localhost:8001/api/analytics/slippage
```

**Circuit Breaker:**
```bash
curl http://localhost:8001/api/circuit
```

**Alerts:**
```bash
curl http://localhost:8001/api/alerts
```

## Container Names

| Service | Container Name |
|---------|----------------|
| Redis | `crypto-trader-redis-1` |
| TimescaleDB | `crypto-trader-timescaledb-1` |
| Grafana | `crypto-trader-grafana-1` |
| Market Scanner | `crypto-trader-market-scanner-1` |
| Qwen Analyzer | `crypto-trader-qwen-analyzer-1` |
| Risk Manager | `crypto-trader-risk-manager-1` |
| Orchestrator | `crypto-trader-orchestrator-1` |
| Strategy Router | `crypto-trader-strategy-router-1` |
| Strategy Scalping | `crypto-trader-strategy-scalping-1` |
| Strategy Swing | `crypto-trader-strategy-swing-1` |
| Strategy Arbitrage | `crypto-trader-strategy-arbitrage-1` |
| Stop-Loss | `crypto-trader-stop-loss-1` |
| Stop-Loss Tracker | `crypto-trader-stop-loss-tracker-1` |
| Feedback | `crypto-trader-feedback-1` |
| Sentiment | `crypto-trader-sentiment-1` |
| Backtesting | `crypto-trader-backtesting-1` |
| Training Export | `crypto-trader-training-export-1` |
| Evolution Agent | `crypto-trader-evolution-agent-1` |
| Watchdog | `crypto-trader-watchdog-1` |
| Monitoring | `crypto-trader-monitoring-1` |
| Dashboard | `crypto-trader-dashboard-1` |
| Circuit Breaker | `crypto-trader-circuit-breaker-1` |
| Regime Detector | `crypto-trader-regime-detector-1` |
| A/B Promoter | `crypto-trader-ab-promoter-1` |
| Paper Trading | `crypto-trader-paper-trading-1` |
| PT Main TF | `crypto-trader-pt-main-tf-1` |
| PT Conservative TF | `crypto-trader-pt-conservative-tf-1` |
| PT Swing TF | `crypto-trader-pt-swing-tf-1` |
| PT HighConf TF | `crypto-trader-pt-highconf-tf-1` |
| PT MultiTF TF | `crypto-trader-pt-multitf-tf-1` |
| PT LowFreq TF | `crypto-trader-pt-lowfreq-tf-1` |
| PT Sentiment TF | `crypto-trader-pt-sentiment-tf-1` |
| PT Aggressive | `crypto-trader-pt-aggressive-1` |
| PT Scalping | `crypto-trader-pt-scalping-1` |
| PT MeanRev | `crypto-trader-pt-meanrev-1` |
| PT Conservative | `crypto-trader-pt-conservative-1` |
| PT Swing | `crypto-trader-pt-swing-1` |
| PT HighConf | `crypto-trader-pt-highconf-1` |
| PT MultiTF | `crypto-trader-pt-multitf-1` |
| PT LowFreq | `crypto-trader-pt-lowfreq-1` |
| PT Sentiment | `crypto-trader-pt-sentiment-1` |
| PT Aggressive NoTF | `crypto-trader-pt-aggressive-notf-1` |
| PT Scalping NoTF | `crypto-trader-pt-scalping-notf-1` |
| PT MeanRev NoTF | `crypto-trader-pt-meanrev-notf-1` |

## Current State (2026-06-18)

### Metrics

- **Total containers:** 40+
- **Paper trading instances:** 20 (10 WITH time filter, 10 WITHOUT)
- **Current streak:** 6 wins
- **Best performer:** UNI/USDT swing (+$26.24, 91% WR)
- **Worst hour:** 11h UTC (-$441.40, death cycle)
- **Optimal hours:** 5,6,8,17,22h UTC

### Analytics (7 days)

**Time Performance:**
| Hour | Trades | WR | PnL |
|------|--------|-----|-----|
| 5h | 6 | 67% | +$1.95 |
| 6h | 8 | 75% | +$0.71 |
| 8h | 17 | 65% | +$2.99 |
| 17h | 8 | 100% | +$0.50 |
| 22h | 14 | 64% | +$5.76 |
| **11h** | **2,392** | **20%** | **-$441.40** |

**Top Symbols:**
| Symbol | Strategy | Trades | WR | PnL |
|--------|----------|--------|-----|-----|
| UNI/USDT | qwen_direct | 6 | 100% | +$30.59 |
| UNI/USDT | swing | 11 | 91% | +$26.24 |
| BNB/USDT | swing | 12 | 100% | +$21.26 |
| DOT/USDT | combined | 14 | 50% | +$15.07 |
| AVAX/USDT | scalping | 41 | 49% | +$11.07 |

**Win Streaks:**
- Max win streak: 24
- Max loss streak: 361 (death cycle)
- Current streak: 6 wins

**Slippage:**
| Strategy | Trades | Fees | PnL |
|----------|--------|------|-----|
| scalping | 662 | $37.33 | -$43.24 |
| close_scalping | 1,136 | $57.85 | -$173.88 |
| swing | 182 | $10.75 | -$29.47 |

## Commands

### Start All Services
```bash
cd ~/Proyectos/crypto-trader
docker compose up -d
```

### Start A/B Test Instances
```bash
docker compose up -d pt-main-tf pt-conservative-tf pt-swing-tf pt-highconf-tf pt-multitf-tf pt-lowfreq-tf pt-sentiment-tf pt-aggressive-notf pt-scalping-notf pt-meanrev-notf
```

### Stop A/B Test Instances
```bash
docker compose stop pt-main-tf pt-conservative-tf pt-swing-tf pt-highconf-tf pt-multitf-tf pt-lowfreq-tf pt-sentiment-tf pt-aggressive-notf pt-scalping-notf pt-meanrev-notf
```

### View Logs
```bash
docker logs crypto-trader-pt-scalping-1 -f
docker logs crypto-trader-circuit-breaker-1 -f
docker logs crypto-trader-monitoring-1 -f
```

### Rebuild Dashboard
```bash
docker compose build dashboard && docker compose up -d dashboard
```

### Query Analytics
```bash
curl http://localhost:8001/api/portfolios
curl http://localhost:8001/api/analytics/time-performance
curl http://localhost:8001/api/analytics/symbol-patterns
curl http://localhost:8001/api/analytics/win-streaks
curl http://localhost:8001/api/analytics/slippage
curl http://localhost:8001/api/alerts
curl http://localhost:8001/api/circuit
```

## Roadmap

### Phase 1: Foundation ✅
- [x] Docker infrastructure
- [x] Market scanner
- [x] Qwen analyzer
- [x] Strategy agents
- [x] Risk manager
- [x] Paper trading

### Phase 2: Intelligence ✅
- [x] Feedback loop
- [x] Strategy router
- [x] Sentiment analyzer
- [x] Backtesting engine
- [x] Training export
- [x] Evolution agent

### Phase 3: Monitoring ✅
- [x] Dashboard
- [x] Watchdog
- [x] Stop-loss tracker
- [x] Analytics endpoints
- [x] A/B testing (20 instances)

### Phase 4: Optimization 🔄
- [x] Time filter (analytics-based)
- [x] A/B test time filter
- [x] Circuit Breaker (autonomous stop on death cycles)
- [x] Real-time alerts (monitoring every 5min)
- [x] A/B Auto-promoter (promotes winners, deprecates losers)
- [x] Dynamic Symbol Exclusion (auto-exclude losing symbols)
- [x] Auto-schedule Tuning (time filter recalculates weekly)
- [x] Regime Detector (market regime classification)
- [x] Fine-tuning Pipeline (stop-loss tracker → training export)
- [x] LLM Graceful Degradation (GPU load adaptation)
- [ ] Freqtrade integration
- [ ] Real exchange dry-run

### Phase 5: Advanced 📋
- [ ] Jesse for MCP-driven AI strategies
- [ ] Hummingbot for market making
- [ ] Gradual capital increase

### Phase 6: Production 📋
- [ ] Real exchange integration
- [ ] Risk management hardening
- [ ] Performance optimization
