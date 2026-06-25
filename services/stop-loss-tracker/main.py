#!/usr/bin/env python3
"""Stop-Loss Tracker - monitors stopped-out signals to see if they would have been profitable."""

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("stop-loss-tracker")

TRACK_WINDOW_MINUTES = 30
CHECK_INTERVAL_SECONDS = 60


class StopLossTracker:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.db: Database | None = None
        self.running = False
        self.tracked_stops: dict[str, dict] = {}
        self.current_prices: dict[str, float] = {}

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        logger.info("Stop-Loss Tracker initialized")

    async def load_tracked_stops(self):
        try:
            data = await self.redis.get_json("stop_loss_tracker:tracked")
            if data:
                self.tracked_stops = data
                logger.info(f"Loaded {len(self.tracked_stops)} tracked stops")
        except Exception as e:
            logger.warning(f"Could not load tracked stops: {e}")

    async def save_tracked_stops(self):
        try:
            await self.redis.set_json("stop_loss_tracker:tracked", self.tracked_stops)
        except Exception as e:
            logger.warning(f"Could not save tracked stops: {e}")

    async def process_trade_result(self, data: dict):
        try:
            if data.get("status") != "closed":
                return

            reasoning = data.get("reasoning", "")
            if "stop loss" not in reasoning.lower():
                return

            order_id = data.get("order_id", "")
            symbol = data.get("symbol", "")
            side = data.get("side", "")
            entry_price = float(data.get("entry_price", 0))
            exit_price = float(data.get("exit_price", 0))
            strategy = data.get("strategy", "")
            stop_loss = data.get("stop_loss")

            if not symbol or not entry_price:
                return

            track_id = f"{order_id}"
            self.tracked_stops[track_id] = {
                "symbol": symbol,
                "side": side,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "stop_loss": stop_loss,
                "strategy": strategy,
                "reasoning": reasoning,
                "stopped_at": datetime.now(timezone.utc).isoformat(),
                "track_until": (datetime.now(timezone.utc) + timedelta(minutes=TRACK_WINDOW_MINUTES)).isoformat(),
                "price_at_stop": exit_price,
                "would_have_been_profitable": None,
                "max_favorable": 0.0,
                "max_adverse": 0.0,
                "final_price": None,
            }

            logger.info(
                f"TRACKING STOP: {side.upper()} {symbol} "
                f"entry=${entry_price:.4f} stop=${exit_price:.4f} "
                f"strategy={strategy}"
            )

            await self.save_tracked_stops()

        except Exception as e:
            logger.error(f"Error processing trade result: {e}")

    async def update_prices(self, data: dict):
        try:
            symbol = data.get("symbol", "")
            price = float(data.get("close", 0))
            if symbol and price > 0:
                self.current_prices[symbol] = price
        except Exception:
            pass

    async def check_tracked_stops(self):
        now = datetime.now(timezone.utc)
        completed = []

        for track_id, track in self.tracked_stops.items():
            symbol = track["symbol"]
            current_price = self.current_prices.get(symbol)

            if not current_price:
                continue

            track_until = datetime.fromisoformat(track["track_until"])
            if now > track_until:
                track["final_price"] = current_price
                entry = track["entry_price"]
                exit_p = track["price_at_stop"]

                if track["side"].lower() == "buy":
                    would_profit = current_price > entry
                    favorable_move = current_price - exit_p
                    adverse_move = exit_p - current_price
                else:
                    would_profit = current_price < entry
                    favorable_move = exit_p - current_price
                    adverse_move = current_price - exit_p

                track["would_have_been_profitable"] = would_profit
                track["max_favorable"] = round(max(track["max_favorable"], favorable_move), 6)
                track["max_adverse"] = round(max(track["max_adverse"], adverse_move), 6)

                completed.append(track_id)

                status = "WOULD HAVE WON" if would_profit else "STILL LOSER"
                logger.info(
                    f"RESULT: {symbol} {track['side']} "
                    f"entry=${entry:.4f} stop=${exit_p:.4f} now=${current_price:.4f} "
                    f"→ {status} "
                    f"max_favorable=${track['max_favorable']:.4f} max_adverse=${track['max_adverse']:.4f}"
                )
            else:
                if track["side"].lower() == "buy":
                    favorable_move = current_price - track["price_at_stop"]
                    adverse_move = track["price_at_stop"] - current_price
                else:
                    favorable_move = track["price_at_stop"] - current_price
                    adverse_move = current_price - track["price_at_stop"]

                track["max_favorable"] = round(max(track["max_favorable"], favorable_move), 6)
                track["max_adverse"] = round(max(track["max_adverse"], adverse_move), 6)

        for track_id in completed:
            del self.tracked_stops[track_id]

        if completed:
            await self.save_tracked_stops()

    async def log_summary(self):
        try:
            rows = await self.db.fetch(
                """SELECT * FROM trades 
                   WHERE status = 'closed' 
                   AND reasoning ILIKE '%stop loss%' 
                   AND time > NOW() - INTERVAL '24 hours'
                   ORDER BY time DESC LIMIT 100"""
            )

            total_stopped = len(rows)
            if total_stopped == 0:
                return

            logger.info(f"\n{'='*60}")
            logger.info(f"STOP-LOSS SUMMARY (last 24h)")
            logger.info(f"{'='*60}")
            logger.info(f"Total stopped out: {total_stopped}")
            logger.info(f"Currently tracked: {len(self.tracked_stops)}")

            if self.tracked_stops:
                profitable = sum(1 for t in self.tracked_stops.values() if t.get("would_have_been_profitable"))
                logger.info(f"Would have been profitable: {profitable}/{len(self.tracked_stops)}")

            logger.info(f"{'='*60}")

        except Exception as e:
            logger.error(f"Error logging summary: {e}")

    async def run(self):
        self.running = True
        await self.initialize()
        await self.load_tracked_stops()

        trade_group = "stop-loss-tracker"
        trade_consumer = "stop-loss-tracker-1"
        market_group = "stop-loss-tracker-market"
        market_consumer = "stop-loss-tracker-market-1"

        logger.info("Stop-Loss Tracker running")

        check_counter = 0
        while self.running:
            try:
                await self.redis.heartbeat("stop-loss-tracker")
                trades = await self.redis.read_stream(
                    settings.STREAM_TRADE_RESULTS, trade_group, trade_consumer, count=10, block=2000,
                )
                for msg_id, data in trades:
                    await self.process_trade_result(data)

                market = await self.redis.read_stream(
                    settings.STREAM_MARKET_DATA, market_group, market_consumer, count=20, block=1000,
                )
                for msg_id, data in market:
                    await self.update_prices(data)

                check_counter += 1
                if check_counter % 10 == 0:
                    await self.check_tracked_stops()

                if check_counter % 300 == 0:
                    await self.log_summary()

            except asyncio.CancelledError:
                self.running = False
                break
            except Exception as e:
                logger.error(f"Stop-Loss Tracker error: {e}")
                await asyncio.sleep(3)


async def main():
    tracker = StopLossTracker()
    await tracker.run()


if __name__ == "__main__":
    asyncio.run(main())
