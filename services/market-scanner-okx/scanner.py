import asyncio
import logging
import sys
from datetime import datetime, timezone

import ccxt.async_support as ccxt
import pandas as pd

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.db import Database
from shared.models import OHLCVData, TechnicalIndicators
from shared.indicators import compute_indicators

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("market-scanner-okx")


class OKXMarketScanner:
    def __init__(self):
        self.exchange = None
        self.redis: RedisClient | None = None
        self.db: Database | None = None
        self.running = False

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        self.exchange = ccxt.okx({
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        })
        await self.exchange.load_markets()
        logger.info(f"OKX Market Scanner initialized with {len(self.exchange.markets)} markets")

    async def fetch_and_publish(self, pair: str, timeframe: str):
        try:
            limit = settings.CANDLE_LIMITS.get(timeframe, 200)
            ohlcv = await self.exchange.fetch_ohlcv(pair, timeframe, limit=limit)
            if not ohlcv or len(ohlcv) < 50:
                return

            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            latest = df.iloc[-1]

            ohlcv_data = OHLCVData(
                symbol=pair,
                timeframe=timeframe,
                timestamp=latest["timestamp"].to_pydatetime(),
                open=float(latest["open"]),
                high=float(latest["high"]),
                low=float(latest["low"]),
                close=float(latest["close"]),
                volume=float(latest["volume"]),
            )
            await self.redis.publish(settings.STREAM_MARKET_DATA, ohlcv_data.model_dump(mode="json"))

            recent_df = df.tail(100).reset_index(drop=True)
            recent_df["close"] = recent_df["close"].astype(float)
            recent_df["high"] = recent_df["high"].astype(float)
            recent_df["low"] = recent_df["low"].astype(float)
            recent_df["volume"] = recent_df["volume"].astype(float)

            indicators_dict = compute_indicators(recent_df)
            indicators = TechnicalIndicators(
                symbol=pair,
                timeframe=timeframe,
                timestamp=latest["timestamp"].to_pydatetime(),
                close=float(latest["close"]),
                **{k: v for k, v in indicators_dict.items() if k in TechnicalIndicators.model_fields},
            )
            await self.redis.publish(settings.STREAM_INDICATORS, indicators.model_dump(mode="json"))

            await self.store_ohlcv(pair, timeframe, df)
            await self.store_indicators(pair, timeframe, indicators)

        except Exception as e:
            logger.error(f"Error fetching {pair} {timeframe}: {e}")

    async def store_ohlcv(self, pair: str, timeframe: str, df: pd.DataFrame):
        try:
            rows = []
            for _, row in df.tail(10).iterrows():
                rows.append((
                    row["timestamp"].to_pydatetime(),
                    pair, timeframe,
                    float(row["open"]), float(row["high"]), float(row["low"]),
                    float(row["close"]), float(row["volume"]),
                ))
            await self.db.execute_many(
                """INSERT INTO ohlcv (time, symbol, timeframe, open, high, low, close, volume)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                   ON CONFLICT DO NOTHING""",
                rows,
            )
        except Exception as e:
            logger.error(f"Error storing OHLCV for {pair} {timeframe}: {e}")

    async def store_indicators(self, pair: str, timeframe: str, ind: TechnicalIndicators):
        try:
            await self.db.execute(
                """INSERT INTO indicators (time, symbol, timeframe, rsi_14, macd_line, macd_signal,
                   macd_hist, bb_upper, bb_middle, bb_lower, ema_9, ema_21, ema_50, atr_14, volume_sma_20)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                   ON CONFLICT DO NOTHING""",
                ind.timestamp, ind.symbol, ind.timeframe,
                ind.rsi_14, ind.macd_line, ind.macd_signal, ind.macd_hist,
                ind.bb_upper, ind.bb_middle, ind.bb_lower,
                ind.ema_9, ind.ema_21, ind.ema_50, ind.atr_14, ind.volume_sma_20,
            )
        except Exception as e:
            logger.error(f"Error storing indicators for {pair} {timeframe}: {e}")

    async def scan_timeframe(self, timeframe: str):
        interval = settings.SCAN_INTERVALS.get(timeframe, 300)
        logger.info(f"Starting OKX scan loop for {timeframe} (interval: {interval}s)")
        while self.running:
            tasks = [self.fetch_and_publish(pair, timeframe) for pair in settings.TOP_PAIRS]
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.redis.heartbeat("market-scanner-okx")
            await asyncio.sleep(interval)

    async def run(self):
        self.running = True
        await self.initialize()
        loops = []
        for tf in settings.TIMEFRAMES:
            loops.append(asyncio.create_task(self.scan_timeframe(tf)))
        logger.info(f"OKX Scanner running with {len(loops)} timeframe tasks")
        try:
            await asyncio.gather(*loops)
        except asyncio.CancelledError:
            self.running = False
        finally:
            if self.exchange:
                await self.exchange.close()
            if self.redis:
                await self.redis.close()
            if self.db:
                await self.db.close()


async def main():
    scanner = OKXMarketScanner()
    await scanner.run()


if __name__ == "__main__":
    asyncio.run(main())
