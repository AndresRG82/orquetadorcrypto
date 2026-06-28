import asyncio
import logging
import sys

sys.path.insert(0, "/app")
from shared.strategy_base import BaseStrategyAgent
from shared.models import TechnicalIndicators, SignalType

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("strategy-swing")

SWING_TIMEFRAMES = {"1h", "4h", "1d"}


class SwingAgent(BaseStrategyAgent):
    strategy_name = "swing"
    allowed_regimes = {"trending_up", "trending_down"}
    param_defaults = {
        "rsi_oversold_deep": 20, "rsi_oversold": 30, "rsi_oversold_weak": 40,
        "rsi_overbought_deep": 80, "rsi_overbought": 70, "rsi_overbought_weak": 60,
        "bb_position_low": 0.1, "bb_position_high": 0.9,
        "atr_tp_multiplier": 3.0, "atr_sl_multiplier": 1.5,
        "min_confidence": 0.55, "min_score_strong": 5, "min_score_weak": 3,
        "active": True, "cooldown_seconds": 3600, "confidence_weight": 1.0,
        "alpha_zoo_enabled": True, "alpha_zoo_weight": 0.3, "alpha_zoo_timeframe": "1h",
    }
    param_redis_key = "strategy:params:swing"
    config_redis_key = "strategy:config:swing"
    heartbeat_key = "strategy-swing"
    consumer_group = "strategy-swing"
    consumer_name = "swing-consumer-1"
    stream_block_ms = 5000
    qwen_timeout = 30.0

    async def evaluate(self, ind: TechnicalIndicators) -> dict | None:
        if ind.timeframe not in SWING_TIMEFRAMES:
            return None

        score = 0
        reasons = []
        p = self.params

        if ind.rsi_14 is not None:
            if ind.rsi_14 < p["rsi_oversold_deep"]:
                score += 3; reasons.append(f"RSI deeply oversold ({ind.rsi_14:.1f})")
            elif ind.rsi_14 < p["rsi_oversold"]:
                score += 2; reasons.append(f"RSI oversold ({ind.rsi_14:.1f})")
            elif ind.rsi_14 < p["rsi_oversold_weak"]:
                score += 1; reasons.append(f"RSI low ({ind.rsi_14:.1f})")
            elif ind.rsi_14 > p["rsi_overbought_deep"]:
                score -= 3; reasons.append(f"RSI deeply overbought ({ind.rsi_14:.1f})")
            elif ind.rsi_14 > p["rsi_overbought"]:
                score -= 2; reasons.append(f"RSI overbought ({ind.rsi_14:.1f})")
            elif ind.rsi_14 > p["rsi_overbought_weak"]:
                score -= 1; reasons.append(f"RSI elevated ({ind.rsi_14:.1f})")

        if all(v is not None for v in [ind.ema_9, ind.ema_21, ind.ema_50]):
            if ind.ema_9 > ind.ema_21 > ind.ema_50:
                score += 2; reasons.append("Strong uptrend: EMA9>21>50")
            elif ind.ema_9 < ind.ema_21 < ind.ema_50:
                score -= 2; reasons.append("Strong downtrend: EMA9<21<50")
            elif ind.ema_9 > ind.ema_21:
                score += 1; reasons.append("Short-term uptrend: EMA9>21")
            elif ind.ema_9 < ind.ema_21:
                score -= 1; reasons.append("Short-term downtrend: EMA9<21")

        if ind.macd_line is not None and ind.macd_signal is not None:
            if ind.macd_line > ind.macd_signal and (ind.macd_hist or 0) > 0:
                score += 2; reasons.append("MACD bullish")
            elif ind.macd_line < ind.macd_signal and (ind.macd_hist or 0) < 0:
                score -= 2; reasons.append("MACD bearish")

        if ind.bb_upper is not None and ind.bb_lower is not None and ind.bb_middle is not None:
            bb_range = ind.bb_upper - ind.bb_lower
            if bb_range > 0:
                pos = (ind.close - ind.bb_lower) / bb_range
                if pos < p["bb_position_low"]:
                    score += 2; reasons.append(f"Price near lower BB ({pos:.2f}) - potential reversal")
                elif pos > p["bb_position_high"]:
                    score -= 2; reasons.append(f"Price near upper BB ({pos:.2f}) - potential reversal")

        if ind.volume_sma_20 is not None and ind.volume_change_pct is not None:
            if ind.volume_change_pct > 100:
                reasons.append(f"Volume surge ({ind.volume_change_pct:.0f}%)")

        signal = SignalType.HOLD
        confidence = 0.3
        if score >= p["min_score_strong"]:
            signal = SignalType.BUY
            confidence = min(0.95, 0.5 + score * 0.08) * p["confidence_weight"]
        elif score >= p["min_score_weak"]:
            signal = SignalType.BUY
            confidence = (0.45 + score * 0.05) * p["confidence_weight"]
        elif score <= -p["min_score_strong"]:
            signal = SignalType.SELL
            confidence = min(0.95, 0.5 + abs(score) * 0.08) * p["confidence_weight"]
        elif score <= -p["min_score_weak"]:
            signal = SignalType.SELL
            confidence = (0.45 + abs(score) * 0.05) * p["confidence_weight"]

        return {"signal": signal, "confidence": confidence,
                "reasoning": "; ".join(reasons) if reasons else "No strong signal",
                "technical_score": score}


async def main():
    agent = SwingAgent()
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())
