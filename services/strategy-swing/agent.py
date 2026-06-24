import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

import httpx

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.models import TechnicalIndicators, TradingSignal, SignalType

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("strategy-swing")

SWING_TIMEFRAMES = {"1h", "4h", "1d"}


class SwingAgent:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.running = False
        self.qwen_url = settings.QWEN_ANALYZER_URL
        self.params: dict = {}

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        await self.load_params()
        logger.info("Swing agent initialized")

    async def load_params(self):
        stored = await self.redis.get_json("strategy:params:swing")
        config = await self.redis.get_json("strategy:config:swing")
        defaults = {
            "rsi_oversold_deep": 20, "rsi_oversold": 30, "rsi_oversold_weak": 40,
            "rsi_overbought_deep": 80, "rsi_overbought": 70, "rsi_overbought_weak": 60,
            "bb_position_low": 0.1, "bb_position_high": 0.9,
            "atr_tp_multiplier": 3.0, "atr_sl_multiplier": 1.5,
            "min_confidence": 0.45, "min_score_strong": 4, "min_score_weak": 2,
            "active": True, "cooldown_seconds": 3600, "confidence_weight": 1.0,
        }
        self.params = {**defaults, **(stored or {}), **(config or {})}
        logger.info(f"Swing params loaded: sl_mult={self.params['atr_sl_multiplier']} tp_mult={self.params['atr_tp_multiplier']}")

    def evaluate_technicals(self, ind: TechnicalIndicators) -> Optional[dict]:
        score = 0
        reasons = []
        p = self.params

        if ind.rsi_14 is not None:
            if ind.rsi_14 < p["rsi_oversold_deep"]:
                score += 3
                reasons.append(f"RSI deeply oversold ({ind.rsi_14:.1f})")
            elif ind.rsi_14 < p["rsi_oversold"]:
                score += 2
                reasons.append(f"RSI oversold ({ind.rsi_14:.1f})")
            elif ind.rsi_14 < p["rsi_oversold_weak"]:
                score += 1
                reasons.append(f"RSI low ({ind.rsi_14:.1f})")
            elif ind.rsi_14 > p["rsi_overbought_deep"]:
                score -= 3
                reasons.append(f"RSI deeply overbought ({ind.rsi_14:.1f})")
            elif ind.rsi_14 > p["rsi_overbought"]:
                score -= 2
                reasons.append(f"RSI overbought ({ind.rsi_14:.1f})")
            elif ind.rsi_14 > p["rsi_overbought_weak"]:
                score -= 1
                reasons.append(f"RSI elevated ({ind.rsi_14:.1f})")

        if all(v is not None for v in [ind.ema_9, ind.ema_21, ind.ema_50]):
            if ind.ema_9 > ind.ema_21 > ind.ema_50:
                score += 2
                reasons.append("Strong uptrend: EMA9>21>50")
            elif ind.ema_9 < ind.ema_21 < ind.ema_50:
                score -= 2
                reasons.append("Strong downtrend: EMA9<21<50")
            elif ind.ema_9 > ind.ema_21:
                score += 1
                reasons.append("Short-term uptrend: EMA9>21")
            elif ind.ema_9 < ind.ema_21:
                score -= 1
                reasons.append("Short-term downtrend: EMA9<21")

        if ind.macd_line is not None and ind.macd_signal is not None:
            if ind.macd_line > ind.macd_signal and (ind.macd_hist or 0) > 0:
                score += 2
                reasons.append("MACD bullish")
            elif ind.macd_line < ind.macd_signal and (ind.macd_hist or 0) < 0:
                score -= 2
                reasons.append("MACD bearish")

        if ind.bb_upper is not None and ind.bb_lower is not None and ind.bb_middle is not None:
            bb_range = ind.bb_upper - ind.bb_lower
            if bb_range > 0:
                pos = (ind.close - ind.bb_lower) / bb_range
                if pos < p["bb_position_low"]:
                    score += 2
                    reasons.append(f"Price near lower BB ({pos:.2f}) - potential reversal")
                elif pos > p["bb_position_high"]:
                    score -= 2
                    reasons.append(f"Price near upper BB ({pos:.2f}) - potential reversal")

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

        return {
            "signal": signal,
            "confidence": confidence,
            "reasoning": "; ".join(reasons) if reasons else "No strong signal",
            "technical_score": score,
        }

    def calculate_targets(self, ind: TechnicalIndicators, signal: SignalType) -> tuple[Optional[float], Optional[float]]:
        if ind.atr_14 is None:
            return None, None

        if signal == SignalType.BUY:
            target = ind.close + ind.atr_14 * self.params["atr_tp_multiplier"]
            stop = ind.close - ind.atr_14 * self.params["atr_sl_multiplier"]
        elif signal == SignalType.SELL:
            target = ind.close - ind.atr_14 * self.params["atr_tp_multiplier"]
            stop = ind.close + ind.atr_14 * self.params["atr_sl_multiplier"]
        else:
            return None, None

        return round(target, 8), round(stop, 8)

    async def query_qwen(self, ind: TechnicalIndicators, tech_result: dict) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = {
                    "symbol": ind.symbol,
                    "timeframe": ind.timeframe,
                    "close": ind.close,
                    "technical_score": tech_result["technical_score"],
                    "technical_reasoning": tech_result["reasoning"],
                }
                resp = await client.post(f"{self.qwen_url}/analyze", json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning(f"Qwen query failed: {e}")
            return None

    async def process_indicator(self, data: dict):
        try:
            ind = TechnicalIndicators(**data)

            if ind.timeframe not in SWING_TIMEFRAMES:
                return

            tech_result = self.evaluate_technicals(ind)

            if tech_result["signal"] == SignalType.HOLD:
                return

            target, stop = self.calculate_targets(ind, tech_result["signal"])

            qwen_result = await self.query_qwen(ind, tech_result)

            final_confidence = tech_result["confidence"]
            final_reasoning = f"[SWING] {tech_result['reasoning']}"

            if qwen_result is None:
                final_confidence = tech_result["confidence"] * 0.5
                final_reasoning += " | Qwen unavailable, confidence reduced"

            if qwen_result:
                try:
                    qwen_signal = qwen_result.get("signal", "hold")
                    qwen_confidence = qwen_result.get("confidence", 0.5)
                    qwen_reasoning = qwen_result.get("reasoning", "")

                    if qwen_signal.lower() == tech_result["signal"].value:
                        final_confidence = (tech_result["confidence"] + qwen_confidence) / 2 + 0.1
                        final_reasoning += f" | Qwen confirms: {qwen_reasoning}"
                    else:
                        final_confidence = tech_result["confidence"] * 0.6
                        final_reasoning += f" | Qwen disagrees ({qwen_signal})"
                except Exception:
                    pass

            final_confidence = min(1.0, max(0.0, final_confidence))

            if final_confidence < self.params["min_confidence"]:
                return

            signal = TradingSignal(
                signal_id=str(uuid.uuid4()),
                symbol=ind.symbol,
                timeframe=ind.timeframe,
                timestamp=datetime.now(timezone.utc),
                signal=tech_result["signal"],
                confidence=final_confidence,
                strategy="swing",
                reasoning=final_reasoning,
                entry_price=ind.close,
                target_price=target,
                stop_loss=stop,
                indicators_snapshot=ind,
            )

            await self.redis.publish(settings.STREAM_SIGNALS, signal.model_dump(mode="json"))
            logger.info(
                f"SWING signal: {signal.signal.value} {signal.symbol} {signal.timeframe} "
                f"conf={signal.confidence:.2f}"
            )

        except Exception as e:
            logger.error(f"Error processing: {e}")

    async def run(self):
        self.running = True
        await self.initialize()
        group = "strategy-swing"
        consumer = "swing-consumer-1"

        logger.info("Swing agent running")
        reload_counter = 0
        while self.running:
            try:
                messages = await self.redis.read_stream(
                    settings.STREAM_INDICATORS, group, consumer, count=10, block=5000,
                )
                for msg_id, data in messages:
                    asyncio.create_task(self.process_indicator(data))
                reload_counter += 1
                if reload_counter % 100 == 0:
                    await self.load_params()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Swing agent error: {e}")
                await asyncio.sleep(5)


async def main():
    agent = SwingAgent()
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())