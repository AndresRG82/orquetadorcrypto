# Crypto Trader — Plan de Mejoras

**Fecha:** 2026-06-18
**Última revisión:** 40+ contenedores activos, 20 paper-trading en paralelo, Sprint 1-3 completados

---

## Estado Actual (2026-06-18 21:30)

### Métricas
| Métrica | Valor |
|---|---|
| Total Containers | 40+ |
| Paper Trading Instances | 20 (10 WITH TF, 10 WITHOUT) |
| Current Streak | 6 wins |
| Best Performer | UNI/USDT swing (+$26.24, 91% WR) |
| Worst Hour | 11h UTC (-$441.40, death cycle) |
| Optimal Hours | 5,6,8,17,22h UTC |

### Infraestructura (40+ contenedores)
- Redis + TimescaleDB
- Ollama (gemma3:4b principal, qwen2.5:3b fallback)
- 20 paper-trading en paralelo (A/B testing time filter)
- vectorbt 1.0.0 para backtesting
- Evolution-agent para auto-optimización
- Watchdog para auto-recovery
- Dashboard con comparación de portfolios
- **Circuit Breaker** (para ante death cycles)
- **Regime Detector** (clasifica régimen de mercado)
- **A/B Auto-promoter** (promueve ganadores)
- **Real-time Alerts** (monitoring cada 5min)
- **Dynamic Symbol Exclusion** (excluye símbolos perdedores)
- **Auto-schedule Tuning** (recalcula horarios óptimos)
- **LLM Graceful Degradation** (adapta a carga GPU)
- **Fine-tuning Pipeline** (stop-loss tracker → JSONL)

### Safety Systems Implementados
| Sistema | Estado | Función |
|---|---|---|
| Circuit Breaker | ✅ | Para ante death cycles (<5min) |
| Regime Detector | ✅ | Clasifica régimen de mercado |
| A/B Auto-promoter | ✅ | Promueve ganadores, depreca perdedores |
| Dynamic Symbol Exclusion | ✅ | Excluye símbolos con pérdidas sostenidas |
| Auto-schedule Tuning | ✅ | Recalcula horarios óptimos semanalmente |
| LLM Graceful Degradation | ✅ | Reduce frecuencia cuando GPU saturada |
| Real-time Alerts | ✅ | Alertas cada 5min |
| Fine-tuning Pipeline | ✅ | Exporta datos para fine-tuning |

### Nuevos Endpoints
```bash
curl http://localhost:8001/api/alerts    # Alertas en tiempo real
curl http://localhost:8001/api/circuit   # Estado del circuit breaker
```

### Nuevas Keys Redis
```
circuit:state          → estado del circuit breaker
circuit:history        → últimos 50 trips
alerts:critical        → stream de alertas
ab:promotions          → historial de promociones
risk:excluded_symbols  → símbolos excluidos dinámicamente
market:regime          → régimen de mercado actual
llm:degradation_level  → estado de carga del LLM
time_filter:schedule   → horarios auto-calculados
```

### Paper-Trading en Paralelo (10 instancias)
| # | Portfolio | Value | PnL | PnL% | Trades | WR |
|---|---|---|---|---|---|---|
| 1 | **Mean Reversion** | $1,007 | **+$6.84** | **+0.7%** | 16 | 62% |
| 2 | **Low-Frequency** | $1,003 | **+$3.48** | **+0.3%** | 0 | 0% |
| 3 | Swing Only | $985 | -$15.38 | -1.5% | 5 | 20% |
| 4 | Scalping Only | $976 | -$23.89 | -2.4% | 16 | 62% |
| 5 | Conservative | $934 | -$66.17 | -6.6% | 338 | 15% |
| 6 | High Confidence | $921 | -$78.77 | -7.9% | 305 | 8% |
| 7 | Multi-TF | $893 | -$106.53 | -10.7% | 426 | 22% |
| 8 | Sentiment | $893 | -$106.53 | -10.7% | 426 | 22% |
| 9 | Aggressive | $886 | -$114.29 | -11.4% | 435 | 23% |
| 10 | Main (all) | $832 | -$168.11 | -16.8% | 0 | 2% |

### Hallazgos Clave
1. **Selectividad gana**: Mean Reversion (+0.7%) y Low-Frequency (+0.3%) son los únicos ganadores
2. **Overtrading mata**: Aggressive (-11.4%) y Multi-TF (-10.7%) pierden por fees/slippage
3. **Win rate alto no garantiza ganancias**: Scalping tiene 62% WR pero pierde por R:R
4. **El Main carga todo junto y diluye ganancias**

### Lo que FUNCIONA
- **Mean Reversion**: +0.7%, 62% WR, 16 trades selectivos
- **Low-Frequency**: +0.3%, 0 trades aún (esperando setups)
- **Scalping**: 62% WR (necesita ajuste R:R)
- **Swing**: 20% WR pero positivo
- **Sentiment analyzer**: Fear & Greed ~25
- **Evolution-agent**: optimizando parámetros
- **Watchdog**: auto-recovery de contenedores
- **Dashboard**: comparación de 10 portfolios en tiempo real

### Lo que NO funciona
- **Main (consolidado)**: -16.8%, diluido por overtrading
- **Aggressive**: -11.4%, demasiados trades
- **Multi-TF**: -10.7%, filter no efectivo
- **Sentiment-Gated**: -10.7%, mismo rendimiento que Multi-TF
- **High Confidence**: -7.9%, pocos setups de 90%+

---

## Fase 1: Completada ✅

### 1.1 Fix qwen-analyzer Pydantic errors ✅
- **Solución**: Structured outputs + fallback gemma3:4b
- **Resultado**: 0 errores Pydantic

### 1.2 Optimizar parámetros de scalping ✅
- **Solución**: Evolution-agent + ajustes manuales
- **Resultado**: SL=2.0 ATR, TP=2.5 ATR, min_score=2

### 1.3 Reducir trades de alta frecuencia ✅
- **Solución**: Cooldown 600s, exclude PEPE/BTC
- **Resultado**: Menos trades, mejor calidad

### 1.4 Risk Manager fixes ✅
- **Solución**: Consecutive losses cooldown, is_closing bypass, cash check fix
- **Resultado**: Sells no bloqueados, cooldown progresivo

### 1.5 Watchdog ✅
- **Solución**: Consumer idle checks, Docker health, restart automático
- **Resultado**: Auto-recovery de contenedores

---

## Fase 2: Paper-Trading A/B Testing (Completada) ✅

### 2.1 Engine parametrizable ✅
- **Solución**: Variables de entorno para filtrar señales
- **Resultado**: 10 configuraciones en paralelo

### 2.2 Dashboard comparativo ✅
- **Solución**: API `/api/portfolios` con todos los portfolios
- **Resultado**: Comparación en tiempo real

### 2.3 Estrategias testeadas ✅
| Estrategia | Resultado | Decisión |
|---|---|---|
| Mean Reversion | +0.7% | **MANTENER** |
| Low-Frequency | +0.3% | **MANTENER** (esperar trades) |
| Scalping | -2.4% | **AJUSTAR** (R:R) |
| Swing | -1.5% | **MANTENER** |
| Conservative | -6.6% | **REVISAR** |
| High Confidence | -7.9% | **REVISAR** |
| Multi-TF | -10.7% | **DESCARTAR** |
| Sentiment | -10.7% | **DESCARTAR** |
| Aggressive | -11.4% | **DESCARTAR** |
| Main | -16.8% | **DESCARTAR** |

---

## Fase 3: Optimización de Estrategias Ganadoras (En curso)

### 3.1 Mean Reversion — Refinar
- **Actual**: RSI <25 compra, RSI >50 venta
- **Mejora**: Agregar Bollinger Bands squeeze, volume confirmation
- **Objetivo**: +1% mensual

### 3.2 Low-Frequency — Monitorear
- **Actual**: 0 trades (MAX_TRADES_PER_DAY=3)
- **Mejora**: Reducir umbral a 1 trade/día
- **Objetivo**: Evaluación después de 48h

### 3.3 Scalping — Ajustar R:R
- **Actual**: 62% WR pero -$23.89
- **Mejora**: Aumentar TP a 3.0 ATR, reducir SL a 1.8 ATR
- **Objetivo**: PnL positivo con 62% WR

### 3.4 Swing — Evaluar
- **Actual**: -1.5%, 20% WR
- **Mejora**: Reducir trades, solo setups fuertes
- **Objetivo**: PnL positivo

---

## Fase 4: Integración Freqtrade (Semana 2)

### Por qué Freqtrade
- Hyperparameter optimization avanzado (Bayesian, genetic algorithms)
- Exchange integration real (Binance, Kraken, etc.)
- Dry-run mode para paper trading
- Comunidad activa + plugins
- Stoploss personalizado trailing

### 4.1 Instalar Freqtrade como container Docker
- Freqtrade tiene imagen oficial: `freqtradeorg/freqtrade`
- Configurar exchange: Binance (paper trading)
- Sincronizar pares: TOP 20
- **Resultado**: Freqtrade como motor de backtesting/hyperopt principal

### 4.2 Migrar estrategias ganadoras a Freqtrade
- Reescribir Mean Reversion y Low-Frequency como Freqtrade strategies
- Usar freqtrade's populate_indicators() + populate_buy_trend()
- Mantener vectorbt para sweep rápido, Freqtrade para optimización profunda
- **Resultado**: Estrategias testeados con Freqtrade's hyperopt

### 4.3 Conectar Freqtrade con nuestro orchestrator
- Freqtrade genera señales → Redis stream → Risk manager → Paper trading
- O usar Freqtrade's dry-run mode directamente
- **Resultado**: Mejores señales + mejor gestión de riesgo

---

## Fase 5: Jesse para MCP-Driven Development (Semana 3)

### Por qué Jesse
- Diseñado para crypto trading con AI
- Event-driven architecture
- Backtesting integrado
- Research-focused (paper trading primero)

### 5.1 Instalar Jesse como container
- `jesse-ai/jesse` Docker image
- Configurar Binance paper trading
- Conectar con Redis para shared state

### 5.2 Desarrollo de estrategias con MCP
- Usar Jesse's strategy framework
- AI-driven parameter optimization
- Backtesting con datos históricos reales

### 5.3 Comparar con Freqtrade
- Evaluar cuál produce mejores resultados
- Mantener ambos como opciones
- Elegir ganador para producción

---

## Fase 6: Hummingbot para Market Making (Semana 4)

### Por qué Hummingbot
- Market making especializado
- Liquidez en exchanges
- Yield earning
- Configuración avanzada de spreads

### 6.1 Configurar Hummingbot
- Instalar container Docker
- Conectar con exchange (paper trading)
- Configurar market making strategies

### 6.2 Estrategias de market making
- **Pure market making**: bid/ask spread
- **Cross-exchange market making**: arbitraje entre exchanges
- **Liquidity mining**: earn fees

### 6.3 Integrar con el sistema
- Hummingbot genera liquidity → Risk manager evalúa
- Coordinar con directional strategies (mean reversion, scalping)
- **Resultado**: Ingresos por fees + directional trading

---

## Fase 7: Producción (Semana 5+)

### 7.1 Señales reales (sin dinero real)
- Cambiar de paper trading a dry-run en exchanges reales
- Validar latencia y execution quality
- Monitoring con Grafana

### 7.2 Capital real (gradual)
- Empezar con $100
- Escalar gradualmente
- Stop-loss estricto: max 2% por trade

### 7.3 Monetización
- Vender señales via Telegram/Discord bot
- Licenciar estrategias
- Revenue share con inversores

---

## Resumen de Prioridades

| Prioridad | Tarea | Timeline | Impacto |
|---|---|---|---|
| COMPLETADO | Circuit Breaker | Sprint 1 | Protección ante death cycles |
| COMPLETADO | Real-time Alerts | Sprint 1 | Monitoreo continuo |
| COMPLETADO | A/B Auto-promoter | Sprint 2 | Capitalización automática |
| COMPLETADO | Dynamic Symbol Exclusion | Sprint 2 | Excluye perdedores |
| COMPLETADO | Auto-schedule Tuning | Sprint 2 | Horarios óptimos |
| COMPLETADO | Regime Detector | Sprint 3 | Contexto de mercado |
| COMPLETADO | Fine-tuning Pipeline | Sprint 3 | Aprendizaje continuo |
| COMPLETADO | LLM Graceful Degradation | Sprint 3 | Estabilidad GPU |
| MEDIA | Freqtrade integration | Semana 4 | Hyperopt avanzado |
| MEDIA | Real exchange dry-run | Semana 4 | Validación real |
| BAJA | Jesse MCP development | Semana 5 | AI strategies |
| BAJA | Hummingbot market making | Semana 6 | Ingresos por fees |
| BAJA | Producción real | Semana 7+ | Revenue |

---

## Comandos Útiles

```bash
# Ver logs en tiempo real
docker compose logs -f --tail=50 <service>

# Verificar estado del sistema
docker compose ps

# Ver portfolios comparados
curl -s http://localhost:8001/api/portfolios | python3 -m json.tool

# Ver métricas del main
curl -s http://localhost:8001/api/stats | python3 -m json.tool

# Ver señales recientes
curl -s http://localhost:8001/api/signals?limit=10 | python3 -m json.tool

# Ver evolution cycle
curl -s http://localhost:8001/api/evolution | python3 -m json.tool

# Ver backtest results
curl -s http://localhost:8001/api/backtest | python3 -m json.tool

# Ver sentiment
curl -s http://localhost:8001/api/sentiment | python3 -m json.tool

# Forzar evolution cycle
docker compose restart evolution-agent

# Reset portfolio específico
curl -X POST http://localhost:8001/api/reset

# Ver trades por estrategia
docker compose exec timescaledb psql -U trader -d trader -c "
  SELECT strategy, COUNT(*) as trades, 
    SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
    ROUND(SUM(pnl_usd)::numeric, 2) as pnl
  FROM trades WHERE status = 'closed' 
  GROUP BY strategy ORDER BY pnl DESC;"
```

---

**Próxima revisión:** 2026-06-19 12:00 (24h)
