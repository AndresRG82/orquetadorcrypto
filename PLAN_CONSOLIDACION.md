# Plan de Implementación — Consolidación, Validación de Flujo y Rentabilidad

**Objetivo general:** Consolidar el sistema → validar el flujo end-to-end sin LLM grande → validar rentabilidad → recién entonces considerar LLM externo y dinero real.

Cada fase tiene un **criterio de salida** (gate). No se avanza a la fase siguiente sin cumplirlo.

> **Actualizado tras investigación previa (ver `respuestas.txt`).** Se encontraron 2 bugs críticos que invalidan resultados actuales y deben corregirse ANTES de cualquier otra cosa. Ver FASE 0 agregada abajo.

---

## FASE 0 — Bugs críticos (bloquean todo lo demás)

**Duración estimada:** 1–2 días
**Meta:** corregir contabilidad de PnL antes de confiar en cualquier métrica histórica o futura.

### 0.1 Bug de slippage no descontado en cierre de posición
- [ ] En `portfolio.close_position()`: `net_pnl = gross_pnl - fee` no resta el slippage de cierre — solo el slippage de apertura impacta (vía `cost`)
- [ ] Corregir para que el slippage de cierre se reste del PnL neto
- [ ] **Impacto:** todo el PnL histórico hasta ahora está inflado. Cualquier número de rentabilidad visto hasta la fecha debe considerarse no confiable hasta corregir esto.

### 0.2 Fees simulados no reflejan OKX real
- [ ] `TRADING_FEE_PCT` fijo en 0.075% para todos los venues — `okx_testnet` y `okx_swap_testnet` nunca recuperan el fee real de la orden, usan el mismo valor fijo que `paper`
- [ ] Ajustar a valores reales: spot taker 0.10%, perp taker 0.05% (ideal: leer el fee real devuelto por la API de OKX en `_place_testnet_order()`)
- [ ] Agregar funding rate (~0.03%/8h) para posiciones swap abiertas más de 8h, actualmente ignorado
- [ ] Ajustar slippage de fijo (0.1%) a un rango por par (0.05%–0.2%), ya que BTC/USDT necesita menos que altcoins de baja liquidez

### ✅ Criterio de salida Fase 0
- PnL recalculado correctamente (slippage de cierre incluido)
- Fees y funding reflejan condiciones reales de OKX, no el valor fijo simulado
- Cualquier métrica de rentabilidad reportada de aquí en adelante usa estos valores corregidos

---

## FASE 1 — Consolidación del sistema

**Duración estimada:** 1–2 semanas
**Meta:** reducir complejidad operativa sin perder funcionalidad.

### 1.1 Auditoría de servicios activos
- [ ] Listar los 40+ servicios con: ¿se usa?, ¿se puede fusionar?, ¿se puede eliminar?
- [ ] Crear tabla: `servicio | activo? | razón de existir | candidato a fusión/eliminación`

### 1.2 Eliminar lo no usado — CONFIRMADO seguro, sin riesgo
- [ ] `services/pt-*` (10 variantes): ya no existen en docker-compose.yml, solo quedan menciones en docs. Borrar carpetas de código si existen, limpiar docs.
- [ ] `services/freqtrade/` (3 bots) + `services/freqtrade-bridge/`: huérfanos, ningún compose los referencia. Eliminar carpetas completas.
- [ ] `services/strategy-router/`: disabled, sin referencias. Eliminar.
- [ ] `services/ab-promoter/`: disabled, sin referencias, no hay A/B activo que promover. Eliminar.

### 1.3 Mover a `docker-compose.experimental.yml` — confirmado seguro
- [ ] `nautilus-bridge` (profile `nautilus`): dashboard y evolution-agent tienen graceful degradation (tabla vacía si no corre). Mover sin riesgo.
- [ ] `swarm-coordinator` (profile `swarm`): mismo caso, graceful degradation confirmada.
- [ ] **Importante:** si en el futuro quieres que el experimental se comunique con el compose principal (ej. swarm ajustando `risk:params.kelly_fraction`), declarar `trader-net` como `external: true` en el experimental — si no, quedan en redes Docker separadas y no se comunican.
- [ ] Confirmar arranque limpio con `docker compose up -d` (solo compose principal) tras mover estos servicios.

### 1.4 Mover a experimental — revisar antes
- [ ] `services/training-export/`: evolution-agent todavía lee `training:export_stats` pero está stale desde que se deshabilitó. Decidir: reactivar o eliminar la lectura en evolution-agent también.
- [ ] `services/feedback/`: disabled, solo watchdog lo referencia. Revisar si watchdog necesita ajuste si se elimina.

### 1.5 Fusionar servicios — confirmado con análisis de código
- [ ] **`stop-loss` + `stop-loss-tracker`**: fusión en un solo proceso es segura — mismo Dockerfile, ciclos compatibles (ambos event-driven), sin streams superpuestos entre sí. De paso, corregir bug detectado: `stop-loss` solo monitorea `paper_trading:state`, no cubre posiciones de `paper-trading-okx` ni `paper-trading-okx-swap`.
- [ ] **3 `strategy-*`**: NO fusionar en un solo proceso (timing distinto — scalping cada 3s vs swing/arbitrage cada 5s — y arbitrage es ~38% distinto al resto). En su lugar:
  - Extraer `BaseStrategyAgent` a `shared/` con la lógica común (~32% compartido entre los 3: init, query_qwen, calculate_targets, run loop)
  - Cada estrategia hereda y solo implementa su lógica específica (`evaluate_technicals` / `check_correlation_divergence`, etc.)
  - Arreglar que `STRATEGY` env var ya existe en compose pero ningún `agent.py` la lee — cada uno hardcodea su nombre en ~13 lugares. Usar `os.getenv("STRATEGY")` + `StrategyFactory`.
  - Mantener 3 contenedores independientes (permite escalar scalping por separado si hace falta)
- [ ] Documentar la decisión final en `AGENTS.md` (actualizar sección "Docker profiles")

### 1.4 Observabilidad mínima
- [ ] Verificar/ampliar `monitoring` para capturar **latencia entre streams**:
  - `market:data` → `market:indicators`
  - `strategy:signals` → `risk:approved`
  - `risk:approved` → `trade:results`
- [ ] Dashboard simple (Grafana, ya existe) con: lag de cada stream, mensajes/seg, errores por servicio
- [ ] Alertas básicas (log o webhook) si un stream deja de recibir mensajes por > N segundos

### ✅ Criterio de salida Fase 1
- `docker compose ps` muestra solo servicios que realmente se usan
- Hay un dashboard que muestra salud del pipeline en tiempo real
- `AGENTS.md` actualizado reflejando la arquitectura real (no la aspiracional)

---

## FASE 2 — Validar el flujo (sin LLM grande)

**Duración estimada:** 1–2 semanas
**Meta:** confirmar que el pipeline es correcto, trazable y determinista, en modo sin LLM o con LLM local mínimo.

### 2.1 Trazabilidad end-to-end — YA EXISTE, no requiere trabajo nuevo
- [x] ~~Instrumentar `trace_id`~~ — **confirmado innecesario.** Ya existe `signal_id` (uuid4 generado en cada strategy agent) que se propaga end-to-end: `signal_id` → `TradingSignal` → `RiskAssessment` → `TradeOrder` → `TradeResult` → `QwenFeedback`. Usar este campo para correlacionar.
- [ ] Para **replay determinista** sí falta algo distinto: un `batch_id`/`run_id` que identifique una ejecución completa de replay (no por señal individual). Generar al inicio del replay e inyectar como constante — esto sí es trabajo nuevo, pero mínimo.
- [ ] Script de verificación: tomar 100 `signal_id` random y confirmar que cada uno completa el flujo o reportar dónde se pierde

### 2.2 Modo baseline sin LLM
- [ ] Confirmar que `risk-manager` y `strategy-*` corren con `OLLAMA_HOST` desconectado/deshabilitado sin crashear
- [ ] Correr el sistema completo 48–72h en este modo, en mock data
- [ ] Registrar: señales generadas, señales aprobadas por risk-manager, trades ejecutados, PnL simulado — esto es tu **baseline puramente técnico**

### 2.3 Replay determinista
- [ ] Grabar un dataset fijo de OHLCV (sintético o histórico real vía OKX) — mínimo 30 días
- [ ] Crear script `replay.py` que alimenta ese dataset al pipeline reemplazando `market-scanner`
- [ ] **Fuentes de no-determinismo confirmadas (ordenadas por impacto), corregir antes del replay:**
  - `np.random.*` sin seed en `mock_scanner.py` (10 llamadas) — agregar seed fijo
  - Ollama con `temperature` 0.1–0.3 en 5 servicios — reemplazar con respuestas grabadas para el replay
  - APIs externas vivas (ccxt OKX, httpx Binance/FearGreed, 15+ llamadas) — reemplazar con datos grabados
  - `datetime.now()` / `time.time()` en cooldowns y circuit-breaker (86+ llamadas en 20+ servicios) — reemplazar por reloj controlado basado en el timestamp del stream, no el reloj real
  - `uuid.uuid4()` para IDs (12 llamadas) — opcional: usar hash de contenido si se necesita determinismo total en los IDs
- [ ] Correr el replay 3 veces con los mismos parámetros tras las correcciones anteriores
- [ ] Verificar: ¿las señales generadas son idénticas en las 3 corridas?

### 2.4 Validación de risk-manager
- [ ] Test manual: inyectar señales sintéticas que deberían ser rechazadas (tamaño excesivo, drawdown superado, símbolo en lista de exclusión del regime-detector) y confirmar que el risk-manager las bloquea
- [ ] Test manual: inyectar condiciones de circuit-breaker (7+ pérdidas seguidas simuladas) y confirmar auto-pausa

### ✅ Criterio de salida Fase 2
- Trazabilidad completa de señal: 100% de las señales se pueden seguir de punta a punta o se sabe exactamente dónde se pierden
- Replay determinista: mismas señales en corridas repetidas (o no-determinismo identificado y documentado como aceptable)
- Risk-manager y circuit-breaker verificados con casos de prueba manuales

---

## FASE 3 — Validar rentabilidad (antes de dinero real)

**Duración estimada:** 6–10 semanas (la más larga, no se debe acortar)
**Meta:** evidencia estadística de que la estrategia es rentable fuera de la muestra de ajuste.

### 3.1 Backtesting con separación in/out-of-sample — ESTADO ACTUAL: CRÍTICAMENTE CONTAMINADO
- [ ] ⚠️ **Confirmado:** no existe train/test split actualmente — `evolution-agent` optimiza sobre los mismos 30 días con los que se evalúa. Parámetros actuales (ej. scalping `rsi_oversold=5`, `atr_sl_multiplier=3.5`) son resultado de grid search sin validación — probable overfitting severo.
- [ ] ⚠️ **Confirmado:** los datos en TimescaleDB son sintéticos (`mock_scanner`), no reales de OKX. `market-scanner-okx` lleva muy poco tiempo corriendo. **No hay suficiente histórico real todavía** — empezar a acumular datos reales ya, en paralelo a todo lo demás (se necesita ~30 días mínimo).
- [ ] Resetear parámetros contaminados: descartar los valores actuales en `strategy:params:*`, re-optimizar desde cero una vez que haya split correcto
- [ ] Implementar split train/test: reservar último 20% como out-of-sample, nunca tocado durante ajuste de `evolution-agent`
- [ ] Para scalping (datos 5m), considerar walk-forward (rolling windows) en lugar de un solo split fijo, dado el volumen de datos disponible
- [ ] Las métricas ya están disponibles — `backtesting` (vectorbt) ya reporta Sharpe, Sortino, Calmar, drawdown, win rate, profit factor, alpha/beta. No hay que construir esto, solo usarlo correctamente con datos no contaminados.

### 3.2 Análisis por régimen de mercado
- [ ] Usar `regime-detector` para etiquetar el histórico por régimen (trending_up, trending_down, ranging, volátil)
- [ ] Correr backtest segmentado por régimen
- [ ] Documentar: ¿la estrategia es rentable en todos los regímenes, o solo en uno? Si es solo en uno, decidir si el sistema debe pausarse fuera de ese régimen (el `regime-detector` ya tiene la lógica de exclusión — verificar que esté bien calibrada)

### 3.3 Costos de transacción realistas — CONFIRMADO: requiere corrección (ver Fase 0.2)
- [ ] Esto ya se cubrió en Fase 0.2 — los venues OKX usan fees simulados idénticos al venue paper (0.0749% fijo), no el fee real de OKX
- [ ] Tras corregir: comparar fills simulados vs reales una vez que `paper-trading-okx` acumule más trades con fees corregidos
- [ ] Re-correr backtest con costos ajustados (fees reales + funding rate + slippage por par + bug de slippage de cierre corregido)

### 3.4 Paper trading extendido en testnet real
- [ ] Mínimo 4–6 semanas corriendo `paper-trading-okx` + `paper-trading-okx-swap` sin interrupciones
- [ ] Mínimo 200 trades acumulados antes de sacar conclusiones (umbral mínimo de significancia estadística)
- [ ] Tracking semanal: PnL acumulado, Sharpe rolling, drawdown máximo, cantidad de activaciones del circuit-breaker

### 3.5 Criterios de decisión cuantitativos (definir ANTES de ver resultados)
- [ ] Definir de antemano: ¿qué Sharpe mínimo? ¿qué drawdown máximo aceptable? ¿cuántas activaciones de circuit-breaker son "demasiadas"?
- [ ] Ejemplo de umbral razonable: Sharpe > 1.0 out-of-sample, max drawdown < 15%, circuit-breaker activado en < 5% del tiempo de corrida
- [ ] Documentar estos umbrales en un archivo `CRITERIOS_RENTABILIDAD.md` — evita la tentación de "mover la meta" después de ver resultados

### ✅ Criterio de salida Fase 3
- Resultados out-of-sample cumplen los umbrales definidos en 3.5
- Rentabilidad sostenida en al menos 2 regímenes de mercado distintos
- Sin overfitting evidente (out-of-sample no muchísimo peor que in-sample)

---

## FASE 4 — Recién aquí: LLM externo y dinero real

Solo después de cumplir las 3 fases anteriores:

- [ ] Evaluar LLM externo (ej. Claude, GPT) **comparando contra el baseline sin LLM de la Fase 2** — el LLM debe demostrar que mejora las métricas, no solo "sonar más inteligente"
- [ ] Empezar con capital real mínimo (ej. el mínimo operable en OKX), no apalancamiento 3x desde el día uno
- [ ] Mantener el modo paper-trading corriendo en paralelo como control continuo

---

## Resumen de gates

| Fase | Gate de salida |
|---|---|
| 0. Bugs críticos | PnL recalculado correctamente, fees/funding reales aplicados |
| 1. Consolidación | Servicios podados, observabilidad de latencia funcionando |
| 2. Validar flujo | Trazabilidad confirmada (signal_id), replay determinista, risk-manager probado |
| 3. Rentabilidad | Umbrales cuantitativos cumplidos out-of-sample, con datos reales, en múltiples regímenes |
| 4. LLM + real | LLM mejora baseline medible, capital real mínimo |
