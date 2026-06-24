#!/usr/bin/env python3
"""Regime Detector - classifies market regime for strategy filtering."""

import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("regime-detector")

UPDATE_INTERVAL = 60
SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT"]

REGIME_STRATEGY_MAP = {
    "trending_up": ["swing"],
    "trending_down": ["swing", "scalping"],
    "ranging": ["scalping"],
    "volatile": [],
}


class RegimeDetector:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.running = False
        self.indicator_cache: dict[str, dict] = {}

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        logger.info("Regime Detector initialized")

    async def run(self):
        await self.initialize()
        self.running = True
        logger.info("Regime Detector running")
        while self.running:
            try:
                await self.detect_and_update()
            except Exception as e:
                logger.error(f"Error detecting regime: {e}")
            await asyncio.sleep(UPDATE_INTERVAL)

    async def detect_and_update(self):
        await self._update_indicator_cache()

        regimes = []
        for symbol in SYMBOLS:
            if symbol in self.indicator_cache:
                indicators = self.indicator_cache[symbol]
                regime = self.detect_regime(indicators)
                regimes.append(regime)

        if regimes:
            avg_confidence = sum(r["confidence"] for r in regimes) / len(regimes)
            regimes.sort(key=lambda x: x["confidence"], reverse=True)
            primary = regimes[0]

            market_regime = {
                "regime": primary["regime"],
                "confidence": round(avg_confidence, 3),
                "price_change": primary["price_change"],
                "volatility": primary["volatility"],
                "bb_width": primary["bb_width"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "symbols_analyzed": len(regimes),
            }

            await self.redis.client.set("market:regime", json.dumps(market_regime))
            logger.info(
                f"Market regime: {market_regime['regime']} "
                f"(confidence={market_regime['confidence']:.2f}, "
                f"volatility={market_regime['volatility']:.3f})"
            )

    async def _update_indicator_cache(self):
        try:
            entries = await self.redis.client.xrevrange(
                "market:indicators", count=50
            )
            for msg_id, fields in entries:
                symbol = fields.get("symbol", "")
                if symbol in SYMBOLS:
                    self.indicator_cache[symbol] = {
                        "rsi_14": self._safe_float(fields.get("rsi_14")),
                        "macd_hist": self._safe_float(fields.get("macd_hist")),
                        "bb_upper": self._safe_float(fields.get("bb_upper")),
                        "bb_lower": self._safe_float(fields.get("bb_lower")),
                        "bb_middle": self._safe_float(fields.get("bb_middle")),
                        "ema_9": self._safe_float(fields.get("ema_9")),
                        "ema_21": self._safe_float(fields.get("ema_21")),
                        "ema_50": self._safe_float(fields.get("ema_50")),
                        "atr_14": self._safe_float(fields.get("atr_14")),
                        "price_change_pct": self._safe_float(fields.get("price_change_pct")),
                        "volume_change_pct": self._safe_float(fields.get("volume_change_pct")),
                        "close": self._safe_float(fields.get("close")),
                        "timestamp": fields.get("timestamp", ""),
                    }
        except Exception as e:
            logger.error(f"Error updating indicator cache: {e}")

    def _safe_float(self, val) -> float:
        try:
            return float(val) if val is not None else 0.0
        except (ValueError, TypeError):
            return 0.0

    def detect_regime(self, ind: dict) -> dict:
        close = ind.get("close", 0)
        rsi = ind.get("rsi_14", 50)
        macd_hist = ind.get("macd_hist", 0)
        ema_9 = ind.get("ema_9", 0)
        ema_21 = ind.get("ema_21", 0)
        bb_upper = ind.get("bb_upper", 0)
        bb_lower = ind.get("bb_lower", 0)
        bb_middle = ind.get("bb_middle", 0)
        price_change = ind.get("price_change_pct", 0)
        atr = ind.get("atr_14", 0)

        ema_diff = 0
        if close > 0 and ema_9 > 0 and ema_21 > 0:
            ema_diff = (ema_9 - ema_21) / close * 100

        bb_width = 0
        if bb_middle > 0 and bb_upper > 0 and bb_lower > 0:
            bb_width = (bb_upper - bb_lower) / bb_middle

        volatility = abs(price_change)
        trend_strength = abs(ema_diff)

        if volatility > 3.0:
            regime = "volatile"
            confidence = min(volatility / 5.0, 1.0)
        elif trend_strength > 0.5:
            if ema_diff > 0:
                regime = "trending_up"
            else:
                regime = "trending_down"
            confidence = min(trend_strength / 2.0, 1.0)
        else:
            regime = "ranging"
            confidence = 1.0 - (trend_strength / 0.5)

        return {
            "regime": regime,
            "confidence": round(max(0.0, min(1.0, confidence)), 3),
            "price_change": round(price_change, 4),
            "volatility": round(volatility, 4),
            "bb_width": round(bb_width, 4),
        }


if __name__ == "__main__":
    detector = RegimeDetector()
    asyncio.run(detector.run())
