import asyncio
import logging
import sys

sys.path.insert(0, "/app")
from shared.strategy_base import BaseStrategyAgent
from shared.models import TechnicalIndicators, SignalType

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("strategy-scalping")

SCALPING_TIMEFRAMES = {"1m", "5m", "15m"}


class ScalpingAgent(BaseStrategyAgent):
    strategy_name = "scalping"
    allowed_regimes = {"ranging", "trending_down"}
    param_defaults = {
        "rsi_oversold_strong": 25, "rsi_oversold_weak": 35,
        "rsi_overbought_strong": 75, "rsi_overbought_weak": 65,
        "bb_position_low": 0.15, "bb_position_high": 0.85,
        "bb_squeeze_threshold": 2.0,
        "atr_tp_multiplier": 3.0, "atr_sl_multiplier": 1.8,
        "min_confidence": 0.4, "min_score": 3,
        "active": True, "cooldown_seconds": 900, "confidence_weight": 1.0,
        "alpha_zoo_enabled": True, "alpha_zoo_weight": 0.3, "alpha_zoo_timeframe": "5m",
    }
    param_redis_key = "strategy:params:scalping"
    config_redis_key = "strategy:config:scalping"
    heartbeat_key = "strategy-scalping"
    consumer_group = "strategy-scalping"
    consumer_name = "scalping-consumer-1"
    stream_block_ms = 3000
    qwen_timeout = 20.0

    async def evaluate(self, ind: TechnicalIndicators) -> dict | None:
        if ind.timeframe not in SCALPING_TIMEFRAMES:
            return None

        exclude = self.params.get("exclude_symbols", [])
        if ind.symbol in exclude:
            return None

        score = 0
        reasons = []
        p = self.params

        if ind.rsi_14 is not None:
            if ind.rsi_14 < p["rsi_oversold_strong"]:
                score += 2; reasons.append(f"RSI oversold ({ind.rsi_14:.1f})")
            elif ind.rsi_14 < p["rsi_oversold_weak"]:
                score += 1; reasons.append(f"RSI approaching oversold ({ind.rsi_14:.1f})")
            elif ind.rsi_14 > p["rsi_overbought_strong"]:
                score -= 2; reasons.append(f"RSI overbought ({ind.rsi_14:.1f})")
            elif ind.rsi_14 > p["rsi_overbought_weak"]:
                score -= 1; reasons.append(f"RSI approaching overbought ({ind.rsi_14:.1f})")

        if ind.macd_hist is not None and ind.macd_signal is not None and ind.macd_line is not None:
            if ind.macd_line > ind.macd_signal and ind.macd_hist > 0:
                score += 1; reasons.append("MACD bullish crossover")
            elif ind.macd_line < ind.macd_signal and ind.macd_hist < 0:
                score -= 1; reasons.append("MACD bearish crossover")

        bb_squeeze = False
        if ind.bb_upper is not None and ind.bb_lower is not None and ind.bb_middle is not None:
            bb_range = ind.bb_upper - ind.bb_lower
            if bb_range > 0 and ind.bb_middle > 0:
                pos = (ind.close - ind.bb_lower) / bb_range
                bb_width_pct = bb_range / ind.bb_middle * 100
                if bb_width_pct < p["bb_squeeze_threshold"]:
                    bb_squeeze = True; reasons.append(f"BB squeeze ({bb_width_pct:.1f}%)")
                if pos < p["bb_position_low"]:
                    score += 2 if bb_squeeze else 1; reasons.append(f"Price near lower BB ({pos:.2f})")
                elif pos > p["bb_position_high"]:
                    score -= 2 if bb_squeeze else 1; reasons.append(f"Price near upper BB ({pos:.2f})")

        if ind.ema_9 is not None and ind.ema_21 is not None:
            if ind.ema_9 > ind.ema_21:
                score += 1; reasons.append("EMA9 > EMA21 (short-term uptrend)")
            else:
                score -= 1; reasons.append("EMA9 < EMA21 (short-term downtrend)")

        if ind.volume_change_pct is not None and ind.volume_change_pct > 50:
            score += (2 if bb_squeeze else 1) if score > 0 else (-2 if bb_squeeze else -1)
            reasons.append(f"Volume spike ({ind.volume_change_pct:.0f}%)")

        if ind.atr_14 is not None:
            atr_pct = ind.atr_14 / ind.close * 100
            if atr_pct > 2.0:
                reasons.append(f"High volatility (ATR%: {atr_pct:.1f}%)")

        signal = SignalType.HOLD
        confidence = 0.3
        if score >= p["min_score"]:
            signal = SignalType.BUY
            confidence = min(0.95, 0.5 + score * 0.1) * p["confidence_weight"]
        elif score <= -p["min_score"]:
            signal = SignalType.SELL
            confidence = min(0.95, 0.5 + abs(score) * 0.1) * p["confidence_weight"]

        return {"signal": signal, "confidence": confidence,
                "reasoning": "; ".join(reasons) if reasons else "No strong signal",
                "technical_score": score}


async def main():
    agent = ScalpingAgent()
    await agent.run()

if __name__ == "__main__":
    asyncio.run(main())
