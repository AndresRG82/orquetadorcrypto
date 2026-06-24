import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional
from collections import defaultdict

import httpx

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.models import TechnicalIndicators, TradingSignal, SignalType

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("strategy-arbitrage")

CORRELATION_PAIRS = {
    "BTC/USDT": ["ETH/USDT", "SOL/USDT", "BNB/USDT"],
    "ETH/USDT": ["BTC/USDT", "SOL/USDT", "LINK/USDT"],
    "SOL/USDT": ["BTC/USDT", "ETH/USDT", "AVAX/USDT"],
}


class ArbitrageAgent:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.running = False
        self.latest_data: dict[str, dict[str, TechnicalIndicators]] = defaultdict(dict)
        self.qwen_url = settings.QWEN_ANALYZER_URL
        self.params: dict = {}

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        await self.load_params()
        logger.info("Arbitrage agent initialized (strict thresholds)")

    async def load_params(self):
        stored = await self.redis.get_json("strategy:params:arbitrage")
        config = await self.redis.get_json("strategy:config:arbitrage")
        defaults = {
            "min_confidence": 0.5,
            "correlation_price_div": 3.0, "correlation_rsi_div": 15,
            "bb_width_threshold": 0.03,
            "bb_position_extreme_high": 0.95, "bb_position_extreme_low": 0.05,
            "atr_tp_multiplier": 2.5, "atr_sl_multiplier": 1.0,
            "active": True, "cooldown_seconds": 7200, "confidence_weight": 1.0,
        }
        self.params = {**defaults, **(stored or {}), **(config or {})}
        logger.info(f"Arbitrage params loaded: bb_width={self.params['bb_width_threshold']} sl_mult={self.params['atr_sl_multiplier']}")

    def check_correlation_divergence(self, ind: TechnicalIndicators) -> Optional[dict]:
        symbol = ind.symbol
        p = self.params
        if symbol not in CORRELATION_PAIRS:
            return None

        correlated = CORRELATION_PAIRS[symbol]
        divergences = []

        for corr_symbol in correlated:
            corr_ind = self.latest_data.get(corr_symbol, {}).get(ind.timeframe)
            if corr_ind is None:
                continue

            if ind.price_change_pct is not None and corr_ind.price_change_pct is not None:
                price_div = ind.price_change_pct - corr_ind.price_change_pct

                if ind.rsi_14 is not None and corr_ind.rsi_14 is not None:
                    rsi_div = ind.rsi_14 - corr_ind.rsi_14

                    if abs(price_div) > p["correlation_price_div"] and abs(rsi_div) > p["correlation_rsi_div"]:
                        divergences.append({
                            "pair": corr_symbol,
                            "price_divergence": price_div,
                            "rsi_divergence": rsi_div,
                        })

        if not divergences:
            return None

        avg_div = sum(d["price_divergence"] for d in divergences) / len(divergences)
        if abs(avg_div) < 2.0:
            return None

        if avg_div > 0:
            signal = SignalType.SELL
            reasoning = f"[ARBITRAGE] {symbol} outperforming peers by {avg_div:.1f}% - mean reversion expected"
        else:
            signal = SignalType.BUY
            reasoning = f"[ARBITRAGE] {symbol} underperforming peers by {abs(avg_div):.1f}% - mean reversion expected"

        confidence = min(0.85, 0.5 + abs(avg_div) * 0.05)

        return {
            "signal": signal,
            "confidence": confidence,
            "reasoning": reasoning,
            "divergences": divergences,
        }

    def check_volatility_squeeze(self, ind: TechnicalIndicators) -> Optional[dict]:
        if ind.bb_upper is None or ind.bb_lower is None or ind.atr_14 is None or ind.bb_middle is None:
            return None
        p = self.params

        bb_width = (ind.bb_upper - ind.bb_lower) / ind.bb_middle if ind.bb_middle and ind.bb_middle > 0 else 0
        atr_pct = ind.atr_14 / ind.close if ind.close > 0 else 0

        if bb_width < p["bb_width_threshold"] and atr_pct < 0.005:
            price_position = (ind.close - ind.bb_lower) / (ind.bb_upper - ind.bb_lower) if ind.bb_upper != ind.bb_lower else 0.5

            volume_confirms = False
            if ind.volume_sma_20 is not None and ind.volume_change_pct is not None:
                if ind.volume_change_pct > 50:
                    volume_confirms = True

            if price_position > p["bb_position_extreme_high"]:
                signal = SignalType.SELL
                confidence = 0.55 if volume_confirms else 0.45
            elif price_position < p["bb_position_extreme_low"]:
                signal = SignalType.BUY
                confidence = 0.55 if volume_confirms else 0.45
            else:
                return None

            if confidence < p["min_confidence"]:
                return None

            reasoning = f"[ARBITRAGE] Vol squeeze on {ind.symbol} BB_width={bb_width:.4f} pos={price_position:.2f}"
            if volume_confirms:
                reasoning += " volume confirms"

            return {
                "signal": signal,
                "confidence": confidence,
                "reasoning": reasoning,
            }

        return None

    async def query_qwen(self, ind: TechnicalIndicators, arb_result: dict) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                payload = {
                    "symbol": ind.symbol,
                    "timeframe": ind.timeframe,
                    "close": ind.close,
                    "technical_score": 0,
                    "technical_reasoning": arb_result["reasoning"],
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
            self.latest_data[ind.symbol][ind.timeframe] = ind

            arb_result = self.check_correlation_divergence(ind)
            if arb_result is None:
                arb_result = self.check_volatility_squeeze(ind)

            if arb_result is None:
                return

            if arb_result["confidence"] < self.params["min_confidence"]:
                return

            qwen_result = await self.query_qwen(ind, arb_result)

            final_confidence = arb_result["confidence"]
            final_reasoning = arb_result["reasoning"]

            if qwen_result:
                try:
                    qwen_signal = qwen_result.get("signal", "hold")
                    qwen_confidence = qwen_result.get("confidence", 0.5)
                    qwen_reasoning = qwen_result.get("reasoning", "")

                    if qwen_signal.lower() == arb_result["signal"].value:
                        final_confidence = (arb_result["confidence"] + qwen_confidence) / 2 + 0.05
                        final_reasoning += f" | Qwen confirms: {qwen_reasoning}"
                    else:
                        final_confidence *= 0.5
                        final_reasoning += f" | Qwen disagrees ({qwen_signal})"
                except Exception:
                    pass

            final_confidence = min(1.0, max(0.0, final_confidence))

            if final_confidence < self.params["min_confidence"]:
                return

            target = None
            stop = None
            if ind.atr_14 is not None:
                if arb_result["signal"] == SignalType.BUY:
                    target = round(ind.close + ind.atr_14 * self.params["atr_tp_multiplier"], 8)
                    stop = round(ind.close - ind.atr_14 * self.params["atr_sl_multiplier"], 8)
                else:
                    target = round(ind.close - ind.atr_14 * self.params["atr_tp_multiplier"], 8)
                    stop = round(ind.close + ind.atr_14 * self.params["atr_sl_multiplier"], 8)

            signal = TradingSignal(
                signal_id=str(uuid.uuid4()),
                symbol=ind.symbol, timeframe=ind.timeframe,
                timestamp=datetime.now(timezone.utc),
                signal=arb_result["signal"], confidence=final_confidence,
                strategy="arbitrage", reasoning=final_reasoning,
                entry_price=ind.close, target_price=target, stop_loss=stop,
                indicators_snapshot=ind,
            )

            await self.redis.publish(settings.STREAM_SIGNALS, signal.model_dump(mode="json"))
            logger.info(f"ARBITRAGE signal: {signal.signal.value} {signal.symbol} {signal.timeframe} conf={signal.confidence:.2f}")

        except Exception as e:
            logger.error(f"Error processing: {e}")

    async def run(self):
        self.running = True
        await self.initialize()
        group = "strategy-arbitrage"
        consumer = "arb-consumer-1"

        logger.info("Arbitrage agent running (strict mode)")
        reload_counter = 0
        while self.running:
            try:
                messages = await self.redis.read_stream(settings.STREAM_INDICATORS, group, consumer, count=10, block=5000)
                for msg_id, data in messages:
                    try:
                        asyncio.create_task(self.process_indicator(data))
                    except Exception:
                        pass
                reload_counter += 1
                if reload_counter % 100 == 0:
                    await self.load_params()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Arbitrage agent error: {e}")
                await asyncio.sleep(5)


async def main():
    agent = ArbitrageAgent()
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
