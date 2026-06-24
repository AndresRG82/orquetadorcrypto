# CryptoTrader

Sistema de trading algorítmico multicontenedor con 40+ microservicios, diseñado para operar con criptomonedas. Actualmente opera en **modo simulado** (`MOCK_MODE=true`) sin exchange real.

## Arquitectura

```
market-scanner → Redis streams → strategy agents → risk-manager → paper-trading → dashboard
       ↓                                                                               ↓
  TimescaleDB                                                                      Grafana
```

**Flujo de datos:**
1. `market-scanner` genera OHLCV sintético y lo publica en `market:data`
2. `qwen-analyzer` calcula indicadores técnicos y los publica en `market:indicators`
3. `strategy-scalping`, `strategy-swing`, `strategy-arbitrage` consumen indicadores y generan señales en `strategy:signals`
4. `risk-manager` valida señales y publica órdenes aprobadas en `risk:approved`
5. `paper-trading` ejecuta órdenes simuladas y registra resultados en `trade:results`
6. `dashboard` + `Grafana` visualizan estado en tiempo real

**Componentes clave:**

| Servicio | Función |
|---|---|
| `redis` | Streams + key-value store (mensajería) |
| `timescaledb` | Base de datos temporal con hypertables |
| `market-scanner` | Generador de datos sintéticos (mock) |
| `qwen-analyzer` | Indicadores técnicos + análisis LLM |
| `strategy-*` | 3 estrategias: scalping, swing, arbitraje |
| `risk-manager` | Gestión de riesgo, position sizing, Kelly |
| `paper-trading` | Ejecución simulada de órdenes |
| `backtesting` | Backtesting con vectorbt |
| `evolution-agent` | Auto-optimización de parámetros |
| `alerts` | Monitoreo y notificaciones |
| `circuit-breaker` | Protección ante death cycles |
| `regime-detector` | Clasificación de régimen de mercado |
| `ab-promoter` | Promoción automática de variantes ganadoras |
| `nautilus-bridge` | Backtesting con NautilusTrader (perfil `nautilus`) |
| `swarm-coordinator` | Multi-agente con Ollama (perfil `swarm`) |

## Requisitos

- Docker + Docker Compose
- GPU NVIDIA (opcional, para Ollama)
- 8 GB RAM mínimo, 16 GB recomendado

## Inicio rápido

```bash
# Clonar
git clone git@github.com:AndresRG82/orquetadorcrypto.git
cd orquetadorcrypto

# Configurar (opcional — usa defaults)
cp .env.example .env

# Iniciar sistema completo
docker compose up -d

# Verificar estado
docker compose ps

# Ver logs
docker compose logs -f dashboard
```

## Modo simulado

Por defecto `MOCK_MODE=true` genera datos OHLCV sintéticos (random walk) para 20 pares y 7 timeframes. Sin exchange real ni credenciales.

Para habilitar componentes adicionales:
```bash
# NautilusTrader (backtesting alternativo)
docker compose --profile nautilus up -d

# Swarm coordinator (análisis multi-agente con Ollama)
docker compose --profile swarm up -d
```

## Perfiles Docker

| Perfil | Servicios |
|---|---|
| `default` | redis, timescaledb, market-scanner, qwen-analyzer, 3 estrategias, risk-manager, paper-trading, dashboard, backtesting, evolution-agent, monitoring, y más |
| `disabled` | 10 variantes paper-trading A/B, 3 Freqtrade, bridge |
| `nautilus` | nautilus-bridge |
| `swarm` | swarm-coordinator |

## Estructura del proyecto

```
├── docker-compose.yml          # 40+ servicios
├── requirements.txt            # Dependencias Python (todas)
├── shared/                     # Paquete compartido
│   ├── config.py               # Configuración centralizada
│   ├── models.py               # Modelos Pydantic
│   ├── redis_client.py         # Cliente Redis singleton
│   ├── db.py                   # Pool asyncpg singleton
│   ├── alpha_zoo/              # 111 factores alfa (B)
│   │   └── zoo/
│   │       ├── academic/       # 10 factores (Fama-French, etc.)
│   │       └── alpha101/       # 101 factores (Kakushadze)
│   └── attribution/            # Atribución de rendimiento (D)
├── services/                   # 25+ microservicios
├── db/init.sql                 # Esquema TimescaleDB
└── .env.example                # Variables de entorno
```

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `MOCK_MODE` | `true` | Activar modo simulado |
| `EXCHANGE` | `binance` | Exchange para datos reales |
| `OLLAMA_HOST` | `http://host.docker.internal:11434` | URL de Ollama |
| `OLLAMA_MODEL` | `gemma3:4b` | Modelo LLM principal |
| `INITIAL_CAPITAL` | `1000.0` | Capital inicial simulado |
| `NAUTILUS_ENABLED` | `false` | Habilitar NautilusTrader |

## Licencia

Uso privado. Factores Alpha101 bajo arXiv:1601.00991 (Kakushadze). Factores académicos basados en Kenneth French data library (price proxies).
