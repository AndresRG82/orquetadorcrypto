import asyncio
import logging
import sys
import json
from datetime import datetime, timezone
from pathlib import Path

import ccxt.async_support as ccxt
import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.db import Database
from shared.models import OHLCVData, TechnicalIndicators

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("market-scanner")


def compute_indicators(df: pd.DataFrame) -> dict:
    result = {}
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    try:
        rsi = RSIIndicator(close, window=14)
        result["rsi_14"] = float(rsi.rsi().iloc[-1]) if not rsi.rsi().iloc[-1] != rsi.rsi().iloc[-1] else None
    except Exception:
        result["rsi_14"] = None

    try:
        macd = MACD(close)
        macd_line = macd.macd().iloc[-1]
        macd_signal = macd.macd_signal().iloc[-1]
        macd_hist = macd.macd_diff().iloc[-1]
        result["macd_line"] = float(macd_line) if macd_line == macd_line else None
        result["macd_signal"] = float(macd_signal) if macd_signal == macd_signal else None
        result["macd_hist"] = float(macd_hist) if macd_hist == macd_hist else None
    except Exception:
        result["macd_line"] = result["macd_signal"] = result["macd_hist"] = None

    try:
        bb = BollingerBands(close)
        result["bb_upper"] = float(bb.bollinger_hband().iloc[-1]) if bb.bollinger_hband().iloc[-1] == bb.bollinger_hband().iloc[-1] else None
        result["bb_middle"] = float(bb.bollinger_mavg().iloc[-1]) if bb.bollinger_mavg().iloc[-1] == bb.bollinger_mavg().iloc[-1] else None
        result["bb_lower"] = float(bb.bollinger_lband().iloc[-1]) if bb.bollinger_lband().iloc[-1] == bb.bollinger_lband().iloc[-1] else None
    except Exception:
        result["bb_upper"] = result["bb_middle"] = result["bb_lower"] = None

    for window, key in [(9, "ema_9"), (21, "ema_21"), (50, "ema_50")]:
        try:
            ema = EMAIndicator(close, window=window)
            val = ema.ema_indicator().iloc[-1]
            result[key] = float(val) if val == val else None
        except Exception:
            result[key] = None

    try:
        atr = AverageTrueRange(high, low, close, window=14)
        result["atr_14"] = float(atr.average_true_range().iloc[-1]) if atr.average_true_range().iloc[-1] == atr.average_true_range().iloc[-1] else None
    except Exception:
        result["atr_14"] = None

    try:
        result["volume_sma_20"] = float(volume.rolling(20).mean().iloc[-1]) if volume.rolling(20).mean().iloc[-1] == volume.rolling(20).mean().iloc[-1] else None
    except Exception:
        result["volume_sma_20"] = None

    result["price_change_pct"] = float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100) if len(close) >= 2 else None
    result["volume_change_pct"] = float((volume.iloc[-1] - volume.iloc[-2]) / volume.iloc[-2] * 100) if len(close) >= 2 and volume.iloc[-2] > 0 else None

    return result


class MarketScanner:
    def __init__(self):
        self.exchange = None
        self.redis: RedisClient | None = None
        self.db: Database | None = None
        self.running = False
        self.active_pairs: list[str] = []

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        exchange_cls = getattr(ccxt, settings.EXCHANGE)
        exchange_config = {"enableRateLimit": True}
        if settings.EXCHANGE_API_KEY:
            exchange_config["apiKey"] = settings.EXCHANGE_API_KEY
            exchange_config["secret"] = settings.EXCHANGE_API_SECRET
        self.exchange = exchange_cls(exchange_config)
        await self.load_active_pairs()
        logger.info(f"Market Scanner initialized with {len(self.active_pairs)} pairs")

    async def load_active_pairs(self):
        try:
            await self.exchange.load_markets()
            markets = self.exchange.markets
            self.active_pairs = [p for p in settings.TOP_PAIRS if p in markets]
            if not self.active_pairs:
                logger.warning("No configured pairs found, falling back to top USDT pairs by volume")
                usdt_pairs = [s for s in markets if s.endswith("/USDT") and markets[s].get("active", True)]
                tickers = await self.exchange.fetch_tickers(usdt_pairs)
                sorted_pairs = sorted(usdt_pairs, key=lambda p: tickers.get(p, {}).get("quoteVolume", 0), reverse=True)
                self.active_pairs = sorted_pairs[:20]
            logger.info(f"Active pairs: {self.active_pairs}")
        except Exception as e:
            logger.error(f"Error loading pairs: {e}")
            self.active_pairs = settings.TOP_PAIRS

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
        logger.info(f"Starting scan loop for {timeframe} (interval: {interval}s)")
        while self.running:
            tasks = [self.fetch_and_publish(pair, timeframe) for pair in self.active_pairs]
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.redis.heartbeat("market-scanner")
            logger.debug(f"Completed scan for {timeframe}, {len(self.active_pairs)} pairs")
            await asyncio.sleep(interval)

    async def run(self):
        self.running = True
        await self.initialize()
        loops = []
        for tf in settings.TIMEFRAMES:
            loops.append(asyncio.create_task(self.scan_timeframe(tf)))
        logger.info(f"Scanner running with {len(loops)} timeframe tasks")
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
    scanner = MarketScanner()
    await scanner.run()


if __name__ == "__main__":
    asyncio.run(main())