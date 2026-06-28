import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import httpx

from shared.config import settings
from shared.redis_client import RedisClient
from shared.models import TechnicalIndicators, TradingSignal, SignalType
from shared.alpha_zoo.integration import AlphaIntegration
from shared.deterministic_id import make_id, enable_replay_mode
from shared.replay_clock import ReplayClock

logger = logging.getLogger("strategy-base")

_REPLAY = os.environ.get("REPLAY_MODE", "").lower() in ("true", "1", "yes")


class BaseStrategyAgent:
    strategy_name: str = ""
    allowed_regimes: set[str] = set()
    param_defaults: dict = {}
    param_redis_key: str = ""
    config_redis_key: str = ""
    heartbeat_key: str = ""
    consumer_group: str = ""
    consumer_name: str = ""
    stream_block_ms: int = 3000
    qwen_timeout: float = 20.0

    def __init__(self):
        self.redis: RedisClient | None = None
        self.running = False
        self.qwen_url = settings.QWEN_ANALYZER_URL
        self.params: dict = {}
        self.alpha = AlphaIntegration()
        self.clock = ReplayClock()
        self.replay_mode = _REPLAY
        if self.replay_mode:
            enable_replay_mode()
            logger.info("REPLAY MODE ENABLED: deterministic IDs, no Qwen, controlled clock")

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        await self.load_params()
        await self.alpha.ensure_db()
        logger.info(f"{self.strategy_name} agent initialized")

    async def load_params(self):
        stored = await self.redis.get_json(self.param_redis_key)
        config = await self.redis.get_json(self.config_redis_key)
        self.params = {**self.param_defaults, **(stored or {}), **(config or {})}

    async def _check_regime(self) -> bool:
        try:
            regime_data = await self.redis.get_json("market:regime")
            if regime_data:
                regime = regime_data.get("regime")
                if regime and regime not in self.allowed_regimes:
                    logger.info(f"Regime '{regime}' not allowed for {self.strategy_name}, skipping")
                    return False
        except Exception:
            pass
        return True

    async def query_qwen(self, ind: TechnicalIndicators, eval_result: dict) -> Optional[dict]:
        if self.replay_mode:
            return None
        try:
            async with httpx.AsyncClient(timeout=self.qwen_timeout) as client:
                payload = {
                    "symbol": ind.symbol,
                    "timeframe": ind.timeframe,
                    "close": ind.close,
                    "technical_score": eval_result.get("technical_score", 0),
                    "technical_reasoning": eval_result.get("reasoning", ""),
                }
                resp = await client.post(f"{self.qwen_url}/analyze", json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning(f"Qwen query failed: {e}")
            return None

    def calculate_targets(self, ind: TechnicalIndicators, signal: SignalType) -> tuple[Optional[float], Optional[float]]:
        if ind.atr_14 is None:
            return None, None

        tp_mult = self.params.get("atr_tp_multiplier", 2.0)
        sl_mult = self.params.get("atr_sl_multiplier", 1.5)

        if signal == SignalType.BUY:
            target = ind.close + ind.atr_14 * tp_mult
            stop = ind.close - ind.atr_14 * sl_mult
        elif signal == SignalType.SELL:
            target = ind.close - ind.atr_14 * tp_mult
            stop = ind.close + ind.atr_14 * sl_mult
        else:
            return None, None

        return round(target, 8), round(stop, 8)

    async def evaluate(self, ind: TechnicalIndicators) -> Optional[dict]:
        raise NotImplementedError

    async def blend_alpha(self, ind: TechnicalIndicators, result: dict) -> dict:
        alpha_enabled = self.params.get("alpha_zoo_enabled", True)
        if not alpha_enabled:
            return result

        alpha_tf = self.params.get("alpha_zoo_timeframe", "5m")
        alpha_weight = self.params.get("alpha_zoo_weight", 0.3)

        await self.alpha.ensure_scores(alpha_tf)
        alpha_score = self.alpha.get_alpha_score(ind.symbol)

        if abs(alpha_score or 0) > 0.01:
            old_signal = result.get("signal", SignalType.HOLD)
            old_confidence = result.get("confidence", 0.5)
            blended_score = result.get("technical_score", 0) * (1 - alpha_weight) + (alpha_score or 0) * alpha_weight

            new_signal = SignalType.BUY if blended_score > 0 else SignalType.SELL if blended_score < 0 else SignalType.HOLD
            new_confidence = min(0.95, old_confidence + abs(alpha_score or 0) * 0.05)

            if new_signal != old_signal and new_signal != SignalType.HOLD:
                result["confidence"] = new_confidence * 0.5
                result["reasoning"] += f" | Alpha disagrees ({new_signal.value}), confidence split"
            else:
                result["confidence"] = min(0.95, new_confidence)
                tweak = "confirms" if new_signal == old_signal else "mixed"
                result["reasoning"] += f" | alpha_zoo={alpha_score:+.3f} ({tweak})"

            result["signal"] = new_signal
        return result

    async def process_indicator(self, data: dict):
        try:
            ind = TechnicalIndicators(**data)

            if not await self._check_regime():
                return

            if self.replay_mode and ind.timestamp:
                self.clock.set_time(ind.timestamp.timestamp())

            eval_result = await self.evaluate(ind)
            if eval_result is None or eval_result.get("signal") == SignalType.HOLD:
                return

            logger.info(
                f"EVAL: {eval_result['signal'].value} {ind.symbol} {ind.timeframe} "
                f"score={eval_result.get('technical_score', 0)} conf={eval_result['confidence']:.2f}"
            )

            eval_result = await self.blend_alpha(ind, eval_result)

            if eval_result.get("signal") == SignalType.HOLD:
                return

            target, stop = self.calculate_targets(ind, eval_result["signal"])

            qwen_result = await self.query_qwen(ind, eval_result)

            final_confidence = eval_result["confidence"]
            final_reasoning = f"[{self.strategy_name.upper()}] {eval_result['reasoning']}"

            if qwen_result is None:
                if not self.replay_mode:
                    final_confidence = eval_result["confidence"] * 0.5
                    final_reasoning += " | Qwen unavailable, confidence reduced"
            else:
                try:
                    qwen_signal = qwen_result.get("signal", "hold")
                    qwen_confidence = qwen_result.get("confidence", 0.5)
                    qwen_reasoning = qwen_result.get("reasoning", "")

                    if qwen_signal.lower() == eval_result["signal"].value:
                        final_confidence = (eval_result["confidence"] + qwen_confidence) / 2 + 0.1
                        final_reasoning += f" | Qwen confirms: {qwen_reasoning}"
                    else:
                        final_confidence = eval_result["confidence"] * 0.5
                        final_reasoning += f" | Qwen disagrees ({qwen_signal}), confidence reduced"
                except Exception:
                    pass

            final_confidence = min(1.0, max(0.0, final_confidence))

            if final_confidence < self.params.get("min_confidence", 0.4):
                return

            signal = TradingSignal(
                signal_id=make_id("sig", strategy=self.strategy_name, symbol=ind.symbol,
                                   timeframe=ind.timeframe, ts=str(ind.timestamp)),
                symbol=ind.symbol,
                timeframe=ind.timeframe,
                timestamp=self.clock.now(),
                signal=eval_result["signal"],
                confidence=final_confidence,
                strategy=self.strategy_name,
                reasoning=final_reasoning,
                entry_price=ind.close,
                target_price=target,
                stop_loss=stop,
                indicators_snapshot=ind,
                batch_id="replay" if self.replay_mode else None,
            )

            await self.redis.publish(settings.STREAM_SIGNALS, signal.model_dump(mode="json"))
            logger.info(
                f"SIGNAL: {signal.signal.value} {signal.symbol} {signal.timeframe} "
                f"conf={signal.confidence:.2f}"
            )

        except Exception as e:
            logger.error(f"Error processing: {e}")

    async def run(self):
        self.running = True
        await self.initialize()

        logger.info(f"{self.strategy_name} agent running")
        reload_counter = 0
        while self.running:
            try:
                await self.redis.heartbeat(self.heartbeat_key)
                messages = await self.redis.read_stream(
                    settings.STREAM_INDICATORS, self.consumer_group, self.consumer_name,
                    count=10, block=self.stream_block_ms,
                )
                for msg_id, data in messages:
                    asyncio.create_task(self.process_indicator(data))

                reload_counter += 1
                if reload_counter % 100 == 0:
                    await self.load_params()
                if reload_counter % 50 == 0 and self.params.get("alpha_zoo_enabled", True):
                    asyncio.create_task(self.alpha.ensure_scores(
                        self.params.get("alpha_zoo_timeframe", "5m")
                    ))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"{self.strategy_name} error: {e}")
                await asyncio.sleep(5)
