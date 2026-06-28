import asyncio
import logging
import sys

sys.path.insert(0, "/app")
from shared.strategy_base import BaseStrategyAgent
from shared.models import TechnicalIndicators, SignalType

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("strategy-arbitrage")

ARBITRAGE_TIMEFRAMES = {"15m", "1h", "4h"}
CORRELATION_PAIRS = [("BTC", "ETH")]


class ArbitrageAgent(BaseStrategyAgent):
    strategy_name = "arbitrage"
    allowed_regimes = {"ranging", "trending_up", "trending_down"}
    param_defaults = {
        "correlation_window": 48, "correlation_threshold": 0.95,
        "divergence_threshold": 2.0, "zscore_mean_period": 48, "zscore_std_period": 48,
        "zscore_entry": 2.0, "zscore_exit": 0.5,
        "volatility_squeeze_threshold": 1.5, "min_bb_squeeze_hours": 6,
        "atr_tp_multiplier": 1.5, "atr_sl_multiplier": 1.2,
        "min_confidence": 0.6,
        "active": True, "cooldown_seconds": 7200, "confidence_weight": 1.0,
        "alpha_zoo_enabled": True, "alpha_zoo_weight": 0.2, "alpha_zoo_timeframe": "1h",
        "rr_min": 1.5,
    }
    param_redis_key = "strategy:params:arbitrage"
    config_redis_key = "strategy:config:arbitrage"
    heartbeat_key = "strategy-arbitrage"
    consumer_group = "strategy-arbitrage"
    consumer_name = "arbitrage-consumer-1"
    stream_block_ms = 5000

    def __init__(self):
        super().__init__()
        self.latest_data: dict[str, dict] = {}

    async def blend_alpha(self, ind: TechnicalIndicators, result: dict) -> dict:
        alpha_enabled = self.params.get("alpha_zoo_enabled", True)
        if not alpha_enabled:
            return result

        alpha_tf = self.params.get("alpha_zoo_timeframe", "1h")
        await self.alpha.ensure_scores(alpha_tf)
        alpha_score = self.alpha.get_alpha_score(ind.symbol)

        if abs(alpha_score or 0) > 0.01:
            result["alpha_boost"] = alpha_score
            result["confidence"] = min(0.95, result["confidence"] + abs(alpha_score) * 0.15)
            result["reasoning"] += f" | alpha_zoo={alpha_score:+.3f} (boosted)"

        return result

    async def check_correlation_divergence(self, ind: TechnicalIndicators) -> dict | None:
        for base_a, base_b in CORRELATION_PAIRS:
            sym_a = f"{base_a}/USDT"
            sym_b = f"{base_b}/USDT"
            if ind.symbol not in (sym_a, sym_b):
                continue

            counterpart = sym_b if ind.symbol == sym_a else sym_a
            if counterpart not in self.latest_data.get("prices", {}):
                continue

            prices = self.latest_data.get("prices", {}).get(counterpart, [])
            if len(prices) < self.params["correlation_window"]:
                continue

            import statistics
            series_a = prices[-self.params["correlation_window"]:]
            series_b_key = ind.symbol + "_prices"
            series_b = self.latest_data.get("prices", {}).get(series_b_key, [])
            if len(series_b) < self.params["correlation_window"]:
                continue

            series_b = series_b[-self.params["correlation_window"]:]
            corr = self._pearson(series_a, series_b)
            zscore_a = (series_a[-1] - statistics.mean(series_a)) / max(statistics.stdev(series_a), 1e-8)
            zscore_b = (series_b[-1] - statistics.mean(series_b)) / max(statistics.stdev(series_b), 1e-8)
            spread = zscore_a - zscore_b

            if abs(spread) > self.params["zscore_entry"]:
                signal = SignalType.BUY if spread < 0 else SignalType.SELL
                confidence = min(0.95, 0.5 + abs(spread) * 0.1)
                reasoning = (
                    f"Correlation ({corr:.2f}) divergence detected "
                    f"zscore_diff={spread:.2f}, {ind.symbol} → {signal.value}"
                )
                return {"signal": signal, "confidence": confidence,
                        "reasoning": reasoning, "technical_score": abs(spread)}

        return None

    async def check_volatility_squeeze(self, ind: TechnicalIndicators) -> dict | None:
        score = 0
        reasons = []

        if ind.bb_upper is not None and ind.bb_lower is not None and ind.bb_middle is not None and ind.bb_upper > ind.bb_lower:
            bb_width_pct = (ind.bb_upper - ind.bb_lower) / ind.bb_middle * 100
            if bb_width_pct < self.params["volatility_squeeze_threshold"]:
                score += 1; reasons.append(f"BB squeeze ({bb_width_pct:.2f}%)")

        if ind.atr_14 is not None:
            atr_pct = ind.atr_14 / max(ind.close, 1e-8) * 100
            if atr_pct > 3.0:
                reasons.append(f"High volatility (ATR%: {atr_pct:.1f})")
            if atr_pct > 5.0:
                score += 1; reasons.append(f"Extreme volatility (ATR%: {atr_pct:.1f})")

        if ind.rsi_14 is not None:
            if ind.rsi_14 < 30:
                score += 1; reasons.append(f"RSI oversold ({ind.rsi_14:.1f})")
            elif ind.rsi_14 > 70:
                score -= 1; reasons.append(f"RSI overbought ({ind.rsi_14:.1f})")

        if not reasons:
            return None

        signal = SignalType.BUY if score > 0 else SignalType.SELL if score < 0 else SignalType.HOLD
        if signal == SignalType.HOLD:
            return None

        confidence = min(0.85, 0.4 + abs(score) * 0.15)
        return {"signal": signal, "confidence": confidence,
                "reasoning": "; ".join(reasons), "technical_score": score}

    async def evaluate(self, ind: TechnicalIndicators) -> dict | None:
        if ind.timeframe not in ARBITRAGE_TIMEFRAMES:
            return None

        result = await self.check_correlation_divergence(ind)
        if result is not None:
            return result

        if ind.timeframe in ("1h", "4h"):
            result = await self.check_volatility_squeeze(ind)
            if result is not None:
                return result

        return None

    def _pearson(self, xs: list[float], ys: list[float]) -> float:
        import statistics
        n = min(len(xs), len(ys))
        if n < 3:
            return 0.0
        xs, ys = xs[:n], ys[:n]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den = max(
            (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5,
            1e-8,
        )
        return max(-1.0, min(1.0, num / den))

    async def process_indicator(self, data: dict):
        if self.latest_data.setdefault("prices", {}).setdefault(data.get("symbol", ""), []):
            self.latest_data["prices"][data["symbol"]].append(data.get("close", 0))
            max_win = max(self.params["correlation_window"] * 2, 100)
            if len(self.latest_data["prices"][data["symbol"]]) > max_win:
                self.latest_data["prices"][data["symbol"]] = (
                    self.latest_data["prices"][data["symbol"]][-max_win:]
                )

        await super().process_indicator(data)


async def main():
    agent = ArbitrageAgent()
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())
