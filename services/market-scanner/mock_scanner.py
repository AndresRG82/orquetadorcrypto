import asyncio
import logging
import sys
import math
import random
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).parent))
from shared.config import settings
from shared.redis_client import RedisClient
from shared.db import Database
from shared.models import OHLCVData, TechnicalIndicators
from scanner import compute_indicators

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("mock-scanner")

STARTING_PRICES = {
    "BTC/USDT": 62000.0, "ETH/USDT": 3400.0, "BNB/USDT": 580.0,
    "XRP/USDT": 0.62, "SOL/USDT": 145.0, "ADA/USDT": 0.48,
    "DOGE/USDT": 0.085, "AVAX/USDT": 35.0, "DOT/USDT": 7.2,
    "LINK/USDT": 18.5, "LTC/USDT": 85.0, "ATOM/USDT": 8.5,
    "UNI/USDT": 7.8, "NEAR/USDT": 5.2, "APT/USDT": 9.0,
    "ARB/USDT": 1.2, "OP/USDT": 2.5, "FIL/USDT": 5.8,
    "SUI/USDT": 1.8, "PEPE/USDT": 0.000008,
}

VOLATILITY = {
    "1m": 0.0008, "5m": 0.002, "15m": 0.004,
    "1h": 0.008, "4h": 0.015, "1d": 0.03, "1w": 0.06,
}

VOLUME_BASE = {
    "BTC/USDT": 500, "ETH/USDT": 3000, "BNB/USDT": 200,
    "XRP/USDT": 50000, "SOL/USDT": 2000, "ADA/USDT": 30000,
    "DOGE/USDT": 100000, "AVAX/USDT": 1000, "DOT/USDT": 1500,
    "LINK/USDT": 2000, "LTC/USDT": 800, "ATOM/USDT": 1200,
    "UNI/USDT": 1500, "NEAR/USDT": 2000, "APT/USDT": 800,
    "ARB/USDT": 3000, "OP/USDT": 2500, "FIL/USDT": 1000,
    "SUI/USDT": 2000, "PEPE/USDT": 500000000,
}


class MockMarketScanner:
    def __init__(self):
        self.redis = None
        self.db = None
        self.running = False
        self.state: dict[str, dict] = {}

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        logger.info(f"Mock Scanner initialized with {len(settings.TOP_PAIRS)} pairs")

    def seed_history(self, pair: str, timeframe: str) -> pd.DataFrame:
        start_price = STARTING_PRICES.get(pair, 100.0)
        vol = VOLATILITY.get(timeframe, 0.002)
        now = datetime.now(timezone.utc)
        tf_map = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}
        minutes = tf_map.get(timeframe, 5)
        n_candles = 200

        timestamps = [now - timedelta(minutes=minutes * (n_candles - i)) for i in range(n_candles)]
        prices = [start_price]
        for _ in range(1, n_candles):
            ret = np.random.normal(0, vol)
            prices.append(prices[-1] * (1 + ret))

        closes = np.array(prices)
        highs = closes * (1 + np.random.uniform(0, vol * 2, n_candles))
        lows = closes * (1 - np.random.uniform(0, vol * 2, n_candles))
        opens = closes * (1 + np.random.uniform(-vol, vol, n_candles))
        volume_base = VOLUME_BASE.get(pair, 1000)
        volumes = np.random.lognormal(mean=0, sigma=0.5, size=n_candles) * volume_base

        for i in range(n_candles):
            opens[i] = closes[i - 1] if i > 0 else closes[i]
            highs[i] = max(opens[i], closes[i], highs[i])
            lows[i] = min(opens[i], closes[i], lows[i])

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        })
        df["timestamp"] = pd.to_datetime([t.isoformat() for t in timestamps])
        return df

    def generate_candle(self, pair: str, timeframe: str, prev_close: float) -> dict:
        vol = VOLATILITY.get(timeframe, 0.002)
        drift = np.random.normal(0.0001, vol)
        close = prev_close * (1 + drift)
        half_spread = abs(close - prev_close) * 0.3
        open_p = prev_close + np.random.uniform(-half_spread, half_spread)
        high = max(open_p, close) * (1 + np.random.uniform(0, vol))
        low = min(open_p, close) * (1 - np.random.uniform(0, vol))
        volume_base = VOLUME_BASE.get(pair, 1000)
        volume = np.random.lognormal(mean=0, sigma=0.3) * volume_base
        return {"open": round(open_p, 8), "high": round(high, 8), "low": round(low, 8), "close": round(close, 8), "volume": round(volume, 2)}

    async def fetch_and_publish(self, pair: str, timeframe: str):
        state_key = f"{pair}:{timeframe}"
        if state_key not in self.state:
            df = self.seed_history(pair, timeframe)
            self.state[state_key] = df
        else:
            df = self.state[state_key]
            prev = df.iloc[-1]["close"]
            candle = self.generate_candle(pair, timeframe, float(prev))
            now = datetime.now(timezone.utc)
            new_row = pd.DataFrame([{
                "timestamp": pd.Timestamp(now),
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "volume": candle["volume"],
            }])
            df = pd.concat([df, new_row], ignore_index=True).tail(300)
            self.state[state_key] = df

        latest = df.iloc[-1]
        ohlcv_data = OHLCVData(
            symbol=pair, timeframe=timeframe,
            timestamp=latest["timestamp"].to_pydatetime(),
            open=float(latest["open"]), high=float(latest["high"]),
            low=float(latest["low"]), close=float(latest["close"]),
            volume=float(latest["volume"]),
        )
        await self.redis.publish(settings.STREAM_MARKET_DATA, ohlcv_data.model_dump(mode="json"))

        recent_df = df.tail(100).reset_index(drop=True)
        for col in ["close", "high", "low", "volume"]:
            recent_df[col] = recent_df[col].astype(float)

        indicators_dict = compute_indicators(recent_df)
        indicators = TechnicalIndicators(
            symbol=pair, timeframe=timeframe,
            timestamp=latest["timestamp"].to_pydatetime(),
            close=float(latest["close"]),
            **{k: v for k, v in indicators_dict.items() if k in TechnicalIndicators.model_fields},
        )
        await self.redis.publish(settings.STREAM_INDICATORS, indicators.model_dump(mode="json"))

        await self.store_ohlcv(pair, timeframe, df)
        await self.store_indicators(pair, timeframe, indicators)

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
            logger.debug(f"Error storing OHLCV: {e}")

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
            logger.debug(f"Error storing indicators: {e}")

    async def scan_timeframe(self, timeframe: str):
        interval = settings.SCAN_INTERVALS.get(timeframe, 300)
        logger.info(f"Starting mock scan for {timeframe} (interval: {interval}s)")
        while self.running:
            tasks = [self.fetch_and_publish(pair, timeframe) for pair in settings.TOP_PAIRS]
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.redis.heartbeat("market-scanner")
            logger.debug(f"Mock scan complete for {timeframe}, {len(settings.TOP_PAIRS)} pairs")
            await asyncio.sleep(interval)

    async def run(self):
        self.running = True
        await self.initialize()
        loops = [asyncio.create_task(self.scan_timeframe(tf)) for tf in settings.TIMEFRAMES]
        logger.info(f"Mock scanner running with {len(loops)} timeframe tasks")
        try:
            await asyncio.gather(*loops)
        except asyncio.CancelledError:
            self.running = False
        finally:
            if self.redis:
                await self.redis.close()
            if self.db:
                await self.db.close()


async def main():
    scanner = MockMarketScanner()
    await scanner.run()


if __name__ == "__main__":
    asyncio.run(main())
