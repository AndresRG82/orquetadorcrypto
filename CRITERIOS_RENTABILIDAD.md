# Criterios de Rentabilidad — Crypto Trader

Fecha de creación: **2026-07-01** (23:59 UTC)

> **NOTA:** Este documento fue definido ANTES de ver los resultados de la
> re-optimización walk-forward de Jul 2026. Ningún criterio aquí refleja
> resultados post-hoc. Los umbrales se basan en:
> - Datos reales del sistema en las 48h previas (411 trades limpios,
>   post-fix de fee bug, post-DB cleanup, post-reset de circuit breaker)
> - Literatura estándar de trading algorítmico (Sharpe, drawdown, sample size)
> - Contexto específico: cripto, intradía/swing, paper → testnet → real

---

## Tabla resumen de umbrales

| # | Criterio | Scalping | Arbitrage | Swing | Global |
|---|----------|----------|-----------|-------|--------|
| 1.1 | Sharpe OOS mínimo (deploy) | ≥ 0.5 | ≥ 0.8 | ≥ 1.0 | — |
| 1.1 | Sharpe OOS mínimo (capital real) | ≥ 0.8 | ≥ 1.2 | ≥ 1.5 | — |
| 1.2 | Profit factor mínimo | ≥ 1.5 | ≥ 1.3 | ≥ 1.5 | — |
| 1.2 | Win rate mínimo (si PF no aplica) | ≥ 35% | ≥ 15% | ≥ 40% | — |
| 1.3 | PnL neto mínimo acumulado (4 sem) | — | — | — | > $200 |
| 2.1 | CB trips por semana (producción) | — | — | — | ≤ 3 |
| 2.2 | Drawdown máximo desde peak | — | — | — | ≤ 15% |
| 2.3 | Paper trading mínimo | — | — | — | 4 semanas |
| 2.3 | Testnet real mínimo | — | — | — | +4 semanas |
| 2.4 | Trades mínimos por estrategia | 100 | 100 | 50 | — |
| 3.1 | LLM externo: rentabilidad testnet | — | — | — | ≥ 4 semanas |
| 3.2 | Costo LLM / beneficio mensual | — | — | — | ≤ 10% |
| 4.1 | Parada positiva anticipada | — | — | — | Sharpe > 2.0 × 2 sem |
| 4.2 | Drawdown máximo para parada | — | — | — | > 25% |

---

## 1. Criterios de rentabilidad por estrategia

### 1.1 Sharpe ratio mínimo OOS

**Fundamento:** El Sharpe ratio mide retorno ajustado por riesgo. Es la métrica
principal de selección porque penaliza la volatilidad, no solo el PnL bruto.

**Contexto del sistema:** El evolution-agent usa EVO_OOS_SHARPE_MIN=0.3 como
umbral para *deployar parámetros* — esto es un mínimo de supervivencia, no de
calidad. Para capital real se necesita más.

**Umbrales:**

| Estrategia | Deploy (evo-agent) | Capital real | Razón |
|------------|-------------------|-------------|-------|
| Scalping | ≥ 0.5 | ≥ 0.8 | Alta frecuencia, ruido inherente. Sharpe 0.5 es punto de partida aceptable en cripto intradía (Lo & MacKinlay, 1999: Sharpe > 0.5 supera al buy-and-hold en mercados eficientes). |
| Arbitrage | ≥ 0.8 | ≥ 1.2 | Debería ser la más estable por ser market-neutral. Si no alcanza 0.8, la estrategia no está capturando ineficiencias reales. |
| Swing | ≥ 1.0 | ≥ 1.5 | Menor frecuencia, tendencias más claras. Puede y debe tener Sharpe más alto. |

**Dato real:** El Sharpe aproximado de las últimas 48h fue:
- qwen_direct: 0.76 (aceptable para deploy, insuficiente para capital real)
- scalping: -1.22 (no pasa ni deploy)
- arbitrage: -15.59 (severamente negativo)

### 1.2 Win rate mínimo y profit factor

**Fundamento:** No todos los sistemas necesitan win rate > 50%. Un sistema con
30% de aciertos puede ser rentable si sus ganancias son 3× mayores que sus
pérdidas. El criterio debe ser **profit factor ≥ umbral** o, alternativamente,
**win rate ≥ umbral Y payoff ratio ≥ 1.5** — no ambos obligatoriamente.

**Contexto real:**
- Scalping: 40.0% WR, profit factor 0.58 → las pérdidas superan a las ganancias.
  Payoff ratio = avg_win/avg_loss ≈ $2.18/$1.58 = 1.38. Si el PF fuera ≥ 1.5,
  este sistema sería rentable aún con 40% WR.
- Arbitrage: 5.9% WR, profit factor 0.03 → catastrófico. Necesita PF ≥ 1.3
  para ser viable. Con 5.9% WR, necesitaría avg_win/avg_loss ≥ 17:1.
- qwen_direct: 39.1% WR, PF 1.30 → más cerca de rentable pero todavía no pasa.

**Umbrales:**

| Estrategia | Profit Factor mínimo | WR mínimo (alternativo) | Payoff Ratio mínimo |
|------------|---------------------|------------------------|---------------------|
| Scalping | ≥ 1.5 | ≥ 35% | ≥ 1.5 |
| Arbitrage | ≥ 1.3 | ≥ 15% | ≥ 3.0 |
| Swing | ≥ 1.5 | ≥ 40% | ≥ 2.0 |

**Razón:** Scalping puede funcionar con WR más bajo si las ganancias son
sistemáticamente mayores. Arbitrage por definición tiene WR bajo — su
viabilidad depende enteramente del payoff ratio. Swing busca WR más alto por su
menor frecuencia.

### 1.3 PnL mínimo acumulado

**Fundamento:** Un sistema que no cubre sus propios costos de transacción no es
rentable, punto. En las últimas 48h:
- Fees totales: $47.44
- Slippage total: $124.48
- Funding: $0.02
- **Costo total de transacción: $171.94** (73.3% del PnL bruto positivo de
  $72.97 se lo comieron los costos — de hecho el neto es -$98.97)

**Umbral:** Período de evaluación de 4 semanas con PnL neto (después de fees,
slippage y funding) positivo y ≥ **$200** (20% del capital inicial de $1000
paper).

**Razón:** $200 en 4 semanas = $50/semana = ~5% retorno mensual. Es modesto
pero realista para el tamaño de cuenta y las condiciones de mercado. Cualquier
estrategia que no pueda generar esto después de cubrir costos simplemente no
es rentable. Este umbral excluye automáticamente estrategias marginales.

---

## 2. Criterios de estabilidad del sistema

### 2.1 Circuit breaker

**Contexto real:** 47 trips del CB en 48 horas (~1 trip/hora). Actualmente
tripped por loss_streak:9. Esto es inaceptable para producción.

**Umbrales:**

| Métrica | Aceptable (paper) | Producción (testnet real) | Razón |
|---------|-------------------|--------------------------|-------|
| Trips/semana | ≤ 10 | ≤ 3 | En paper, trips son señal de diagnóstico. En producción, cada trip es dinero no ganado. |
| Trip por loss_streak | Ocasional | ≤ 1/mes | Un loss_streak de 9 trades seguidos sugiere que la estrategia no distingue condiciones de mercado. |
| Trip por drawdown_5min | ≤ 2/semana | 0 | Drawdown de 5% en 5 min es evento extremo. Más de 2 por semana indica riesgo estructural. |

**Distinción:** Un trip legítimo ocurre en condiciones de mercado adversas
reales (ej. noticia macro, crash repentino). Un trip estructural ocurre porque
la estrategia consistentemente pierde en ciertas condiciones — el patrón de
loss_streak:9 del sistema actual es estructural, no aleatorio.

### 2.2 Drawdown máximo

**Contexto real:** El sistema alcanzó $976 de $1000 inicial (2.4% drawdown
máximo en 48h). El CB tiene umbral de 5% en ventana de 5 minutos.

**Umbrales:**

| Horizonte | Drawdown máximo aceptable | Razón |
|-----------|--------------------------|-------|
| Ventana 5 min (CB) | 5% | Ya configurado. Protege contra spikes. |
| Día | 8% | Pérdida diaria que empieza a ser preocupante. |
| Semana | 12% | Requiere pausa y revisión. |
| Período total evaluación | 15% | Si se pierde 15% del capital en paper, en real sería devastador. |

**Razón:** El 15% máximo sobre capital inicial es consistente con la regla
general de trading: "nunca arriesgues más del 1-2% por trade y no más del
15-20% en total antes de detenerte y revisar" (Schwager, "Market Wizards").

### 2.3 Período mínimo de evaluación

**Contexto real:** 48h de datos no son concluyentes. 411 trades en ese período
son ~205 trades/día, pero la ventana es demasiado corta para cubrir diferentes
regímenes de mercado.

**Umbrales (duración):**

| Fase | Duración mínima | Condiciones |
|------|----------------|-------------|
| Paper trading (actual) | 4 semanas | Métricas 1.1-1.3 saludables las últimas 2 semanas |
| Testnet real (OKX) | +4 semanas (8 total) | Métricas saludables + sin errores de conexión/ejecución |
| Capital real (mínimo) | +0 (tras 8 sem saludables) | Capital inicial reducido (10-20% del portfolio planeado) |
| Capital real (completo) | +4 semanas real (12 total) | Mismas métricas en entorno real |

**Razón:** El mercado cripto tiene ciclos semanales (weekend effect,
liquidaciones de futuros los viernes). Cuatro semanas cubren al menos un mes
completo de condiciones variadas. Ocho semanas cubren dos ciclos.

### 2.4 Muestra mínima de trades

**Contexto real:** Scalping 114 trades limpios, arbitrage 317, qwen_direct 105.
Estos son posteriores a la limpieza de duplicados (fix 2b).

**Umbrales:**

| Estrategia | Trades para Sharpe significativo | Razón |
|------------|--------------------------------|-------|
| Scalping | ≥ 100 | 30 es el mínimo estadístico (CLT). 100 da margen para outliers. Actualmente cumple (114). |
| Arbitrage | ≥ 100 | Cumple ampliamente (317). Pero la calidad importa más que la cantidad con PF=0.03. |
| Swing | ≥ 50 | Menor frecuencia. Actualmente no aplica (excluido). |
| Nueva estrategia | ≥ 30 | Mínimo para que Sharpe tenga algún significado. |

**Razón:** El teorema central del límite sugiere que n ≥ 30 es suficiente
para aproximar normalidad en la distribución de medias muestrales. Pero en
trading, donde la distribución de retornos tiene colas gruesas, se necesita
más. 100 trades es un mínimo práctico ampliamente aceptado en backtesting
(López de Prado, "Advances in Financial Machine Learning", 2018: "Never
accept backtest results with fewer than 100 trades").

---

## 3. Criterios para LLM externo

### 3.1 ¿Cuándo reemplazar Ollama local?

El sistema actual usa gemma3:4b (primario) y qwen2.5:3b (fallback). El LLM
local tiene costo marginal $0 (el cómputo ya está pagado). Reemplazarlo por
un LLM externo (Claude, GPT-4) tiene costo real.

**Condiciones necesarias (ambas deben cumplirse):**

1. **El sistema es rentable en testnet real por ≥ 4 semanas consecutivas.**
   No tiene sentido mejorar un motor si el auto ni siquiera prende.

2. **Evidencia medible de que el LLM local es el cuello de botella.**
   - El *dissent rate* de Qwen (>20% de propuestas rechazadas por el
     risk-manager o por el human-in-the-loop durante 2 semanas seguidas)
   - O: el sistema produce señales correctas pero no las ejecuta por
     razones ajenas al LLM (ej. el problema es de slippage/execution, no
     de análisis)

**Lo que NO justifica el cambio:**
- "Un modelo más grande podría ser mejor" sin evidencia
- Resultados de benchmarks genéricos que no reflejan el dominio específico
- Una corazonada

### 3.2 Costo de LLM externo vs beneficio esperado

**Estimación de costos mensuales (referencia Jul 2026):**

| Servicio | Costo estimado/mes | Notas |
|----------|-------------------|-------|
| Ollama local (actual) | $0 | GPU ya pagada, electricidad marginal |
| Claude Sonnet 4 | ~$15-30 | ~500 requests/día, ~500 tokens c/u |
| Claude Opus | ~$60-120 | ~500 requests/día, ~500 tokens c/u |
| GPT-4o | ~$20-40 | Similar a Sonnet |

**Regla:** El costo del LLM externo no debe exceder el **10% del PnL mensual
del sistema**. Si el sistema genera $300/mes, el LLM debe costar ≤ $30/mes.

**Razón:** Si el LLM se come más del 10% de las ganancias, el sistema está
trabajando para pagar el LLM, no para generar retorno al operador.

---

## 4. Criterio de parada anticipada

### 4.1 Parada anticipada POSITIVA

Se puede acortar el período de evaluación de 4 semanas si:

- **Sharpe OOS > 2.0 sostenido por 2 semanas consecutivas**
  - Con ≥ 200 trades/semana en scalping o ≥ 100 en swing
  - Y drawdown máximo < 5% en el período
- **O:** PnL neto > $500 (50% del capital inicial) en menos de 4 semanas
  - Con drawdown máximo < 8%

**Razón:** Rendimiento excepcional y consistente merece acelerar el cronograma.
La probabilidad de que sea ruido disminuye con Sharpe > 2.0 sostenido.

### 4.2 Parada anticipada NEGATIVA

El experimento debe detenerse y revisarse si:

- **Drawdown > 25% del capital inicial** (de $1000 → < $750)
  - Razón: el próximo 25% sería catastrófico en real. Mejor detener y rediseñar.
- **PnL neto negativo después de 8 semanas sin mejora en las últimas 4**
  - Si en la semana 8 el sistema sigue perdiendo dinero y la tendencia no mejora,
    es improbable que mejore en la semana 12.
- **CB trips > 20/semana por 3 semanas consecutivas**
  - Indica riesgo estructural no aleatorio.

**Acción al detonar parada negativa:**
1. Congelar todos los parámetros actuales
2. Exportar todas las métricas a un archivo de diagnóstico
3. Revisión de cada estrategia individualmente
4. Decisión: rediseñar, pausar, o abandonar

---

## 5. Notas metodológicas

### 5.1 Período de evaluación

Todos los criterios se evalúan sobre **trades cerrados limpios** (post-fix de
fee bug, post-DB cleanup de duplicados). Los 923 trades marcados como
`ghost_closed` no cuentan.

### 5.2 Ventanas de evaluación

Cada criterio se evalúa en **ventanas semanales rodantes** (lunes a domingo
UTC). Sharpe, PF, WR se recalculan al final de cada semana.

### 5.3 Umbrales ajustables

Los umbrales de la sección 1 (por estrategia) pueden ajustarse por estrategia
individualmente si una estrategia muestra consistentemente un perfil
riesgo/retorno diferente al esperado. Cualquier ajuste debe registrarse en la
sección de Historial de revisiones con el motivo y los datos que lo justifican.

---

## Historial de revisiones

| Fecha | Versión | Cambio | Motivo |
|-------|---------|--------|--------|
| — | — | — | — |
