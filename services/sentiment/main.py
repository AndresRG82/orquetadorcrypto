import asyncio
import json
import logging
import sys
from datetime import datetime, timezone

import httpx

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("sentiment")


class SentimentAnalyzer:
    FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
    BINANCE_FUTURES_URL = "https://fapi.binance.com/fapi/v1/fundingRate"

    def __init__(self):
        self.redis: RedisClient | None = None
        self.running = False
        self.fear_greed: dict = {}
        self.funding_rates: dict[str, float] = {}
        self.scan_interval = 300

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        logger.info("Sentiment Analyzer initialized")

    async def fetch_fear_greed(self):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self.FEAR_GREED_URL)
                resp.raise_for_status()
                data = resp.json()
                if data.get("data"):
                    entry = data["data"][0]
                    self.fear_greed = {
                        "value": int(entry["value"]),
                        "classification": entry.get("value_classification", "Neutral"),
                        "timestamp": entry.get("timestamp", ""),
                    }
                    await self.redis.set_json("sentiment:fear_greed", self.fear_greed)
                    logger.info(f"Fear & Greed: {self.fear_greed['value']} ({self.fear_greed['classification']})")
        except Exception as e:
            logger.warning(f"Failed to fetch Fear & Greed: {e}")

    async def fetch_funding_rates(self):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                params = {"limit": 1}
                symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT"]
                rates = {}
                for symbol in symbols:
                    try:
                        params_s = {**params, "symbol": symbol}
                        resp = await client.get(self.BINANCE_FUTURES_URL, params=params_s)
                        resp.raise_for_status()
                        data = resp.json()
                        if data:
                            rate = float(data[0].get("fundingRate", 0))
                            rates[symbol] = rate * 100
                    except Exception:
                        continue
                self.funding_rates = rates
                await self.redis.set_json("sentiment:funding_rates", rates)

                extreme_long = {s: r for s, r in rates.items() if r > 0.05}
                extreme_short = {s: r for s, r in rates.items() if r < -0.05}
                if extreme_long:
                    logger.info(f"Funding extreme long: {extreme_long}")
                if extreme_short:
                    logger.info(f"Funding extreme short: {extreme_short}")

                if rates:
                    avg = sum(rates.values()) / len(rates)
                    logger.info(f"Average funding rate: {avg:.4f}%")
        except Exception as e:
            logger.warning(f"Failed to fetch funding rates: {e}")

    def get_sentiment_signal(self) -> dict:
        fg_value = self.fear_greed.get("value", 50)
        fg_class = self.fear_greed.get("classification", "Neutral")

        signal = "neutral"
        confidence_adj = 0.0
        reasoning = ""

        if fg_value <= 20:
            signal = "contrarian_buy"
            confidence_adj = 0.05
            reasoning = f"Extreme Fear ({fg_value}) - contrarian buy opportunity"
        elif fg_value <= 35:
            signal = "cautious_buy"
            confidence_adj = 0.03
            reasoning = f"Fear ({fg_value}) - slight buy bias"
        elif fg_value >= 80:
            signal = "contrarian_sell"
            confidence_adj = 0.05
            reasoning = f"Extreme Greed ({fg_value}) - contrarian sell signal"
        elif fg_value >= 65:
            signal = "cautious_sell"
            confidence_adj = 0.03
            reasoning = f"Greed ({fg_value}) - slight sell bias"

        avg_funding = 0
        if self.funding_rates:
            avg_funding = sum(self.funding_rates.values()) / len(self.funding_rates)
            if avg_funding > 0.05:
                reasoning += f" | High funding ({avg_funding:.3f}%) = crowded long, bearish"
                confidence_adj += 0.02
            elif avg_funding < -0.03:
                reasoning += f" | Negative funding ({avg_funding:.3f}%) = crowded short, bullish"
                confidence_adj += 0.02

        result = {
            "fear_greed_value": fg_value,
            "fear_greed_classification": fg_class,
            "avg_funding_rate": avg_funding,
            "sentiment_signal": signal,
            "confidence_adjustment": confidence_adj,
            "reasoning": reasoning,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return result

    async def run(self):
        self.running = True
        await self.initialize()

        logger.info("Sentiment Analyzer running")
        while self.running:
            try:
                await self.fetch_fear_greed()
                await self.fetch_funding_rates()

                sentiment = self.get_sentiment_signal()
                await self.redis.set_json("sentiment:current", sentiment)
                await self.redis.publish("sentiment:updates", sentiment)

                await asyncio.sleep(self.scan_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Sentiment error: {e}")
                await asyncio.sleep(60)


async def main():
    analyzer = SentimentAnalyzer()
    await analyzer.run()


if __name__ == "__main__":
    asyncio.run(main())
