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
from shared.alpha_zoo.integration import AlphaIntegration

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("strategy-scalping")

SCALPING_TIMEFRAMES = {"1m", "5m", "15m"}


class ScalpingAgent:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.running = False
        self.qwen_url = settings.QWEN_ANALYZER_URL
        self.params: dict = {}
        self.alpha = AlphaIntegration()

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        await self.load_params()
        await self.alpha.ensure_db()
        logger.info("Scalping agent initialized")

    async def load_params(self):
        stored = await self.redis.get_json("strategy:params:scalping")
        config = await self.redis.get_json("strategy:config:scalping")
        defaults = {
            "rsi_oversold_strong": 25, "rsi_oversold_weak": 35,
            "rsi_overbought_strong": 75, "rsi_overbought_weak": 65,
            "bb_position_low": 0.15, "bb_position_high": 0.85,
            "bb_squeeze_threshold": 2.0,
            "atr_tp_multiplier": 3.0, "atr_sl_multiplier": 1.8,
            "min_confidence": 0.4, "min_score": 3,
            "active": True, "cooldown_seconds": 900, "confidence_weight": 1.0,
            "alpha_zoo_enabled": True, "alpha_zoo_weight": 0.3, "alpha_zoo_timeframe": "5m",
        }
        self.params = {**defaults, **(stored or {}), **(config or {})}
        logger.info(f"Scalping params loaded: sl_mult={self.params['atr_sl_multiplier']} tp_mult={self.params['atr_tp_multiplier']}")

    def evaluate_technicals(self, ind: TechnicalIndicators) -> Optional[dict]:
        score = 0
        reasons = []
        p = self.params

        if ind.rsi_14 is not None:
            if ind.rsi_14 < p["rsi_oversold_strong"]:
                score += 2
                reasons.append(f"RSI oversold ({ind.rsi_14:.1f})")
            elif ind.rsi_14 < p["rsi_oversold_weak"]:
                score += 1
                reasons.append(f"RSI approaching oversold ({ind.rsi_14:.1f})")
            elif ind.rsi_14 > p["rsi_overbought_strong"]:
                score -= 2
                reasons.append(f"RSI overbought ({ind.rsi_14:.1f})")
            elif ind.rsi_14 > p["rsi_overbought_weak"]:
                score -= 1
                reasons.append(f"RSI approaching overbought ({ind.rsi_14:.1f})")

        if ind.macd_hist is not None and ind.macd_signal is not None and ind.macd_line is not None:
            if ind.macd_line > ind.macd_signal and ind.macd_hist > 0:
                score += 1
                reasons.append("MACD bullish crossover")
            elif ind.macd_line < ind.macd_signal and ind.macd_hist < 0:
                score -= 1
                reasons.append("MACD bearish crossover")

        bb_squeeze = False
        if ind.bb_upper is not None and ind.bb_lower is not None and ind.bb_middle is not None:
            bb_range = ind.bb_upper - ind.bb_lower
            if bb_range > 0 and ind.bb_middle > 0:
                pos = (ind.close - ind.bb_lower) / bb_range
                bb_width_pct = bb_range / ind.bb_middle * 100
                if bb_width_pct < p["bb_squeeze_threshold"]:
                    bb_squeeze = True
                    reasons.append(f"BB squeeze ({bb_width_pct:.1f}%)")
                if pos < p["bb_position_low"]:
                    score += 2 if bb_squeeze else 1
                    reasons.append(f"Price near lower BB ({pos:.2f})")
                elif pos > p["bb_position_high"]:
                    score -= 2 if bb_squeeze else 1
                    reasons.append(f"Price near upper BB ({pos:.2f})")

        if ind.ema_9 is not None and ind.ema_21 is not None:
            if ind.ema_9 > ind.ema_21:
                score += 1
                reasons.append("EMA9 > EMA21 (short-term uptrend)")
            else:
                score -= 1
                reasons.append("EMA9 < EMA21 (short-term downtrend)")

        if ind.volume_change_pct is not None and ind.volume_change_pct > 50:
            if score > 0:
                score += 2 if bb_squeeze else 1
            elif score < 0:
                score -= 2 if bb_squeeze else 1
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
            async with httpx.AsyncClient(timeout=20.0) as client:
                payload = {
                    "symbol": ind.symbol,
                    "timeframe": ind.timeframe,
                    "close": ind.close,
                    "technical_score": tech_result["technical_score"],
                    "technical_reasoning": tech_result["reasoning"],
                }
                resp = await client.post(
                    f"{self.qwen_url}/analyze",
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning(f"Qwen query failed: {e}")
            return None

    async def process_indicator(self, data: dict):
        try:
            ind = TechnicalIndicators(**data)

            if ind.timeframe not in SCALPING_TIMEFRAMES:
                return

            exclude = self.params.get("exclude_symbols", [])
            if ind.symbol in exclude:
                return

            tech_result = self.evaluate_technicals(ind)

            if self.params.get("alpha_zoo_enabled", True):
                await self.alpha.ensure_scores(self.params.get("alpha_zoo_timeframe", "5m"))
                blended_score, alpha_note = self.alpha.blend(
                    tech_result["technical_score"],
                    ind.symbol,
                    weight=self.params.get("alpha_zoo_weight", 0.3),
                )
                if alpha_note:
                    tech_result["technical_score"] = blended_score
                    tech_result["alpha_score"] = self.alpha.get_alpha_score(ind.symbol)
                    tech_result["reasoning"] += f" | {alpha_note}"
                    score = blended_score
                    if score >= self.params["min_score"]:
                        tech_result["signal"] = SignalType.BUY
                        tech_result["confidence"] = min(0.95, 0.5 + score * 0.1) * self.params["confidence_weight"]
                        tech_result["confidence"] *= 1.0 + abs(self.alpha.get_alpha_score(ind.symbol)) * 0.05
                    elif score <= -self.params["min_score"]:
                        tech_result["signal"] = SignalType.SELL
                        tech_result["confidence"] = min(0.95, 0.5 + abs(score) * 0.1) * self.params["confidence_weight"]
                        tech_result["confidence"] *= 1.0 + abs(self.alpha.get_alpha_score(ind.symbol)) * 0.05
                    else:
                        tech_result["signal"] = SignalType.HOLD
                    tech_result["confidence"] = min(0.95, tech_result["confidence"])

            if tech_result["signal"] == SignalType.HOLD:
                return

            logger.info(f"EVAL: {tech_result['signal'].value} {ind.symbol} {ind.timeframe} score={tech_result['technical_score']} conf={tech_result['confidence']:.2f}")

            target, stop = self.calculate_targets(ind, tech_result["signal"])

            qwen_result = await self.query_qwen(ind, tech_result)

            final_confidence = tech_result["confidence"]
            final_reasoning = f"[SCALPING] {tech_result['reasoning']}"

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
                        final_confidence = tech_result["confidence"] * 0.5
                        final_reasoning += f" | Qwen disagrees ({qwen_signal}), confidence reduced"
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
                strategy="scalping",
                reasoning=final_reasoning,
                entry_price=ind.close,
                target_price=target,
                stop_loss=stop,
                indicators_snapshot=ind,
            )

            await self.redis.publish(settings.STREAM_SIGNALS, signal.model_dump(mode="json"))
            logger.info(
                f"SCALPING signal: {signal.signal.value} {signal.symbol} {signal.timeframe} "
                f"conf={signal.confidence:.2f}"
            )

        except Exception as e:
            logger.error(f"Error processing indicator: {e}")

    async def run(self):
        self.running = True
        await self.initialize()
        group = "strategy-scalping"
        consumer = "scalping-consumer-1"

        logger.info("Scalping agent running")
        reload_counter = 0
        while self.running:
            try:
                messages = await self.redis.read_stream(
                    settings.STREAM_INDICATORS, group, consumer, count=10, block=3000,
                )
                for msg_id, data in messages:
                    asyncio.create_task(self.process_indicator(data))
                reload_counter += 1
                if reload_counter % 100 == 0:
                    await self.load_params()
                if reload_counter % 50 == 0 and self.params.get("alpha_zoo_enabled", True):
                    asyncio.create_task(self.alpha.ensure_scores(self.params.get("alpha_zoo_timeframe", "5m")))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scalping agent error: {e}")
                await asyncio.sleep(5)


async def main():
    agent = ScalpingAgent()
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())