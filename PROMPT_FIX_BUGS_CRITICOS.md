# Prompt: corregir bugs críticos de contabilidad (Fase 0)

```
Necesito corregir dos bugs de contabilidad confirmados en el sistema, antes de seguir con cualquier otro trabajo. Implementá ambos con cuidado y mostrame el diff antes de aplicar cambios definitivos.

## Bug 1: Slippage de cierre no se resta del PnL neto

En portfolio.close_position(), actualmente:
  net_pnl = gross_pnl - fee

El slippage de cierre se calcula/registra pero nunca se descuenta del PnL neto. Solo el slippage de apertura impacta indirectamente (vía el campo `cost` al abrir la posición).

Tareas:
1. Encontrá el método close_position() y confirmá cómo se calcula el slippage de cierre actualmente (¿ya existe la variable, solo no se resta? ¿hay que calcularla de cero?)
2. Corregí para que: net_pnl = gross_pnl - fee - slippage_cierre
3. Verificá que el slippage de apertura NO se reste dos veces (ya impacta vía `cost` al abrir) — el fix es solo para el cierre.
4. Buscá todos los lugares donde se llama o se loggea net_pnl (TimescaleDB, Redis, dashboard) para confirmar que el cambio se propaga correctamente sin romper nada que dependa del valor anterior.
5. Si hay datos históricos en TimescaleDB con el cálculo viejo, NO los recalcules automáticamente — solo señalame cuántos registros existen con el bug, para que yo decida si quiero recalcular el histórico o solo aplicar el fix desde ahora.

## Bug 2: Fees simulados no reflejan OKX real

Actualmente TRADING_FEE_PCT es un valor fijo (0.075%) usado para los 3 venues (paper, okx_testnet, okx_swap_testnet) por igual. okx_testnet y okx_swap_testnet nunca recuperan el fee real devuelto por la API de OKX en _place_testnet_order() — solo devuelve precio y cantidad.

Tareas:
1. Para el venue `paper` (simulación pura, sin exchange real): mantener un valor fijo, pero corregirlo a valores realistas por tipo de operación:
   - Spot taker: 0.10%
   - Perp taker: 0.05%
   - Si se quiere distinguir maker/taker en el futuro, dejar la estructura preparada (parámetro, no hardcodeado), aunque por ahora el sistema solo usa market orders (taker)

2. Para `okx_testnet` y `okx_swap_testnet` (ejecución real en sandbox): modificá _place_testnet_order() para que recupere y devuelva el fee real reportado por la respuesta de la API de OKX, en lugar de aplicar el valor fijo simulado. Si la respuesta de OKX no incluye el fee directamente, buscá el endpoint correcto (fills/trade history) para obtenerlo.

3. Agregar funding rate para posiciones swap (okx_swap_testnet) abiertas más de 8 horas:
   - Valor por defecto si no se puede consultar en vivo: ~0.03% cada 8h (documentar que es un estimado, no el valor real de mercado)
   - Si la API de OKX expone el funding rate actual, preferir consultarlo en vivo
   - Aplicar el costo de funding al PnL de posiciones swap abiertas, prorrateado por tiempo

4. Ajustar SLIPPAGE_PCT de un valor fijo (0.1%) a un valor configurable por par (ej. diccionario o función), con BTC/USDT y ETH/USDT en el rango bajo (0.05%) y altcoins de baja liquidez en el rango alto (0.2%). Si no hay datos suficientes para calibrar por par individualmente, al menos separar "majors" vs "altcoins" como categorías.

5. Actualizar .env.example y la documentación (AGENTS.md / README.md) reflejando los nuevos valores y la lógica de cálculo, ya que actualmente documentan el valor fijo viejo.

## Verificación final

Después de ambos fixes:
1. Corré py_compile sobre todos los archivos modificados
2. Mostrame un resumen del diff completo antes de que yo confirme
3. Generá 5-10 trades de prueba en modo paper para confirmar que el PnL se calcula con la nueva lógica (slippage de cierre restado, fees nuevos aplicados) y que los números tienen sentido (compará un trade de ejemplo a mano vs lo que reporta el sistema)
4. NO toques el código de evolution-agent ni de los parámetros de estrategia — eso es un problema separado (overfitting, Fase 3) y no debe mezclarse con este fix de contabilidad
```
