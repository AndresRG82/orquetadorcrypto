# CryptoTrader

Sistema de trading algorítmico multicontenedor con 40+ microservicios, comunicación vía Redis streams, almacenamiento en TimescaleDB, e inteligencia artificial con Ollama.

Opera en **OKX testnet** con datos reales de mercado, ejecución de órdenes reales en sandbox, y soporte para **futuros perpetual swap con apalancamiento 3x**.

## Arquitectura

```
market-scanner (mock/OKX) → Redis streams → strategy agents → risk-manager → paper-trading → trade:results
       ↓                                                 ↓                          ↓
  TimescaleDB                                     circuit-breaker             Dashboard + Grafana
       ↓                                                 ↓
  market-scanner-okx (real OHLCV)              regime-detector + sentiment
```

**Flujo de datos:**
1. `market-scanner` (mock) + `market-scanner-okx` (OKX real) publican OHLCV en `market:data`
2. `qwen-analyzer` calcula indicadores técnicos + análisis LLM en `market:indicators`
3. `strategy-scalping`, `strategy-swing`, `strategy-arbitrage` generan señales en `strategy:signals`
4. `risk-manager` valida señales con position sizing dinámico y publica en `risk:approved`
5. `regime-detector` clasifica mercado (trending/ranging/volátil) → ajusta exclusión de símbolos
6. `circuit-breaker` monitorea pérdidas/drawdown/spikes → auto-pausa si es necesario
7. `sentiment` provee Fear & Greed + funding rates como contexto adicional
8. `paper-trading` ejecuta simulación, `paper-trading-okx` ejecuta en OKX testnet (spot), `paper-trading-okx-swap` ejecuta en OKX testnet (swap 3x)
9. `dashboard` + `Grafana` visualizan estado en tiempo real

**Nota:** El sistema NO requiere Ollama para operar — los strategy agents pueden funcionar sin LLM, el risk-manager tiene auto-ajuste opcional.

## Requisitos

- Docker + Docker Compose v2
- GPU NVIDIA (opcional, para Ollama)
- 4 GB RAM mínimo, 8 GB recomendado

## Inicio rápido

```bash
git clone git@github.com:AndresRG82/orquetadorcrypto.git
cd orquetadorcrypto

# Configurar (opcional — usa defaults simulados)
cp .env.example .env
# Editar .env para añadir credenciales OKX (opcional, para testnet real)
#   OKX_API_KEY=...
#   OKX_API_SECRET=...
#   OKX_API_PASSWORD=...
#   MOCK_MODE=true   # false para usar datos reales de OKX
#   EXCHANGE=okx

# Iniciar sistema completo
docker compose up -d

# Verificar estado
docker compose ps

# Ver logs del dashboard
docker compose logs -f dashboard
```

## Venues de ejecución

| Venue | Descripción | Apalancamiento |
|---|---|---|
| `paper` | Simulación en memoria (sin exchange) | 1x |
| `okx_testnet` | Órdenes reales en OKX sandbox (spot) | 1x |
| `okx_swap_testnet` | Perpetual swaps en OKX sandbox | 3x |

## Servicios activos

| Servicio | Función |
|---|---|
| `redis` | Streams + key-value store (mensajería) |
| `timescaledb` | Base de datos temporal con hypertables |
| `market-scanner` | Generador de datos sintéticos (mock) |
| `market-scanner-okx` | OHLCV real desde OKX |
| `qwen-analyzer` | Indicadores técnicos + análisis LLM |
| `strategy-*` | 3 estrategias: scalping, swing, arbitraje |
| `risk-manager` | Gestión de riesgo + RiskAutoTuner AI |
| `orchestrator` | Coordinación y persistencia de señales |
| `paper-trading` | Ejecución simulada |
| `paper-trading-okx` | Ejecución en OKX testnet (spot) |
| `paper-trading-okx-swap` | Ejecución en OKX testnet (swap 3x) |
| `dashboard` | API + UI en puerto 8001 |
| `grafana` | Dashboards visuales en puerto 3000 |
| `circuit-breaker` | Protección ante pérdidas consecutivas |
| `regime-detector` | Clasificación de régimen de mercado |
| `sentiment` | Fear & Greed + funding rates |
| `stop-loss` | Trailing stop-loss automático |
| `stop-loss-tracker` | Análisis de stops ejecutados |
| `monitoring` | Snapshots + health checks |
| `backtesting` | Backtesting con vectorbt |
| `evolution-agent` | Auto-optimización de parámetros con LLM |
| `watchdog` | Auto-recuperación de contenedores |

## Perfiles Docker

| Perfil | Servicios |
|---|---|
| `default` | Todos los servicios activos |
| `nautilus` | nautilus-bridge (backtesting alternativo) |
| `swarm` | swarm-coordinator (análisis multi-agente) |

## Características principales

- **SL/TP en exchange**: stop-loss y take-profit se envían como órdenes de exchange con relleno a mercado (`tpOrdPx=-1`, `slOrdPx=-1`)
- **RiskAutoTuner**: IA ajusta dinámicamente drawdown, loss streak, posición máxima según rendimiento reciente
- **SL/TP dinámicos**: stop-loss tracker evalúa si stops cancelados habrían sido rentables
- **Circuit breaker**: detección de death cycles (7+ pérdidas consecutivas, >60% loss rate, >5% drawdown en 5min, spikes de señales)
- **Regime detector**: clasifica mercado como trending_up/trending_down/ranging/volátil, excluye símbolos de alto riesgo automáticamente
- **Sentiment**: Fear & Greed Index + funding rates de Binance Futures como señal de sobrecompra/sobreventa
- **Alpha Zoo**: 111 factores alfa (academic + alpha101) para estrategias cuantitativas
- **Futures swap**: apalancamiento 3x en OKX testnet con contabilidad basada en margen

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `MOCK_MODE` | `true` | `false` para usar datos reales de OKX |
| `EXCHANGE` | `okx` | Exchange para datos del scanner principal |
| `OKX_API_KEY` | — | API key de OKX (testnet o real) |
| `OKX_API_SECRET` | — | API secret de OKX |
| `OKX_API_PASSWORD` | — | API passphrase de OKX |
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | URL de Ollama |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Modelo LLM para análisis |
| `INITIAL_CAPITAL` | `1000.0` | Capital inicial simulado |
| `MAX_POSITION_PCT` | `0.20` | Máximo 20% del capital por posición |
| `SLIPPAGE_PCT` | `0.001` | Deslizamiento simulado (0.1%) |
| `TRADING_FEE_PCT` | `0.00075` | Comisión simulada (0.075%) |

## Estructura

```
├── docker-compose.yml          # Todos los servicios
├── requirements.txt            # Dependencias Python
├── shared/                     # Paquete compartido
│   ├── config.py               # Configuración centralizada (Pydantic Settings)
│   ├── models.py               # Modelos Pydantic (OHLCVData, TradingSignal, etc.)
│   ├── redis_client.py         # Cliente Redis singleton con streams
│   ├── db.py                   # Pool asyncpg singleton
│   ├── indicators.py           # compute_indicators() compartido
│   └── alpha_zoo/              # 111 factores alfa
├── services/                   # 25+ microservicios
├── db/init.sql                 # Esquema TimescaleDB
└── .env.example                # Variables de entorno
```
