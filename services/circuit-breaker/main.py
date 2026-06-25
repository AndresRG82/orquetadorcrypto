#!/usr/bin/env python3
"""Circuit Breaker - stops the system automatically on destructive patterns in real-time."""

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
logger = logging.getLogger("circuit-breaker")

CHECK_INTERVAL = 10

RULES = {
    "consecutive_losses": 7,
    "loss_rate_window_min": 10,
    "loss_rate_threshold": 0.60,
    "drawdown_window_min": 5,
    "drawdown_threshold": 0.05,
    "signal_spike_factor": 5,
    "pause_duration_min": 20,
    "critical_pause_min": 45,
    "history_max": 50,
}


class CircuitBreaker:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.db: Database | None = None
        self.running = False

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        logger.info("Circuit Breaker initialized")

    async def run(self):
        await self.initialize()
        self.running = True
        logger.info("Circuit Breaker running")
        while self.running:
            try:
                state = await self.evaluate()
                await self.redis.client.set("circuit:state", json.dumps(state))
                if state["status"] == "tripped":
                    logger.warning(f"CIRCUIT TRIPPED: {state['reason']} (resumes at {state['resume_at']})")
            except Exception as e:
                logger.error(f"Error evaluating circuit: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    async def evaluate(self) -> dict:
        state = {
            "status": "open",
            "reason": None,
            "resume_at": None,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }

        existing = await self.redis.client.get("circuit:state")
        if existing:
            existing_state = json.loads(existing)
            if existing_state.get("status") == "tripped":
                if existing_state.get("resume_at", "") > datetime.now(timezone.utc).isoformat():
                    return existing_state

        streak = await self._get_loss_streak()
        if streak >= RULES["consecutive_losses"]:
            return await self._trip(f"loss_streak:{streak}", RULES["pause_duration_min"])

        loss_rate = await self._get_loss_rate(RULES["loss_rate_window_min"])
        if loss_rate >= RULES["loss_rate_threshold"]:
            return await self._trip(f"loss_rate:{loss_rate:.0%}", RULES["pause_duration_min"])

        drawdown = await self._get_drawdown(RULES["drawdown_window_min"])
        if drawdown >= RULES["drawdown_threshold"]:
            return await self._trip(f"drawdown:{drawdown:.1%}", RULES["critical_pause_min"])

        spike = await self._get_signal_spike()
        if spike >= RULES["signal_spike_factor"]:
            return await self._trip(f"signal_spike:{spike:.1f}x", RULES["pause_duration_min"])

        return state

    async def _trip(self, reason: str, pause_min: int) -> dict:
        resume_at = (datetime.now(timezone.utc) + timedelta(minutes=pause_min)).isoformat()
        state = {
            "status": "tripped",
            "reason": reason,
            "resume_at": resume_at,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self._record_trip(state)
        return state

    async def _record_trip(self, state: dict):
        try:
            await self.redis.client.xadd("alerts:critical", {
                "type": "circuit_breaker",
                "status": "tripped",
                "reason": state["reason"],
                "resume_at": state["resume_at"],
            })
            history = await self.redis.client.lrange("circuit:history", 0, RULES["history_max"] - 1)
            await self.redis.client.lpush("circuit:history", json.dumps(state))
            if len(history) >= RULES["history_max"]:
                await self.redis.client.ltrim("circuit:history", 0, RULES["history_max"] - 1)
        except Exception as e:
            logger.error(f"Error recording trip: {e}")

    async def _get_loss_streak(self) -> int:
        try:
            rows = await self.db.fetch("""
                SELECT pnl_usd FROM trades 
                WHERE status = 'closed' AND time > NOW() - INTERVAL '2 hours'
                ORDER BY time DESC LIMIT 20
            """)
            streak = 0
            for r in rows:
                if r["pnl_usd"] < 0:
                    streak += 1
                else:
                    break
            return streak
        except Exception:
            return 0

    async def _get_loss_rate(self, window_min: int) -> float:
        try:
            rows = await self.db.fetch(f"""
                SELECT COUNT(*) as total,
                    SUM(CASE WHEN pnl_usd < 0 THEN 1 ELSE 0 END) as losses
                FROM trades 
                WHERE status = 'closed' 
                AND time > NOW() - INTERVAL '{window_min} minutes'
            """)
            if rows and rows[0]["total"] > 0:
                return rows[0]["losses"] / rows[0]["total"]
            return 0.0
        except Exception:
            return 0.0

    async def _get_drawdown(self, window_min: int) -> float:
        try:
            rows = await self.db.fetch(f"""
                SELECT SUM(pnl_usd) as total_pnl
                FROM trades 
                WHERE status = 'closed' 
                AND time > NOW() - INTERVAL '{window_min} minutes'
            """)
            if rows and rows[0]["total_pnl"]:
                return abs(rows[0]["total_pnl"]) / settings.INITIAL_CAPITAL
            return 0.0
        except Exception:
            return 0.0

    async def _get_signal_spike(self) -> float:
        try:
            current = await self.redis.client.xlen("strategy:signals") or 0
            avg_key = "circuit:avg_signals"
            avg = await self.redis.client.get(avg_key)
            if avg is None:
                await self.redis.client.set(avg_key, str(current), ex=3600)
                return 1.0
            avg_val = float(avg)
            if avg_val == 0:
                return 1.0
            return current / avg_val
        except Exception:
            return 1.0


if __name__ == "__main__":
    cb = CircuitBreaker()
    asyncio.run(cb.run())
