#!/usr/bin/env python3
"""Portfolio monitoring - snapshots every 6h + real-time alerts every 5min."""

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient

import httpx

DASHBOARD_URL = "http://dashboard:8000"
LOG_DIR = Path("/app/logs/monitoring")

ALERT_THRESHOLDS = {
    "pnl_drop_1h": -20.0,
    "wr_drop_30min": 0.25,
    "open_positions_max": 12,
    "drawdown_total": 0.08,
}

MONITOR_INTERVALS = {
    "snapshot": 6 * 3600,
    "metrics": 5 * 60,
    "health": 1 * 60,
}


class MonitoringService:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.running = False
        self.last_snapshot_time = 0
        self.last_metrics_time = 0

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("Monitoring service initialized")

    async def run(self):
        await self.initialize()
        self.running = True
        logger.info("Monitoring service running")
        while self.running:
            try:
                await self.redis.heartbeat("monitoring")
                now = time.time()

                if now - self.last_metrics_time >= MONITOR_INTERVALS["metrics"]:
                    await self.check_metrics()
                    self.last_metrics_time = now

                if now - self.last_snapshot_time >= MONITOR_INTERVALS["snapshot"]:
                    await self.take_snapshot()
                    self.last_snapshot_time = now

            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
            await asyncio.sleep(MONITOR_INTERVALS["health"])

    async def take_snapshot(self):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{DASHBOARD_URL}/api/portfolios")
                portfolios = resp.json()

            snapshot = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "portfolios": portfolios,
            }

            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_file = LOG_DIR / f"monitor_{date_str}.jsonl"

            with open(log_file, "a") as f:
                f.write(json.dumps(snapshot) + "\n")

            logger.info(f"Snapshot saved: {log_file}")
        except Exception as e:
            logger.error(f"Error taking snapshot: {e}")

    async def check_metrics(self):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{DASHBOARD_URL}/api/portfolios")
                portfolios = resp.json()

            alerts = []

            circuit_data = await self.redis.client.get("circuit:state")
            if circuit_data:
                circuit = json.loads(circuit_data)
                if circuit.get("status") == "tripped":
                    alerts.append({
                        "type": "circuit_breaker",
                        "severity": "critical",
                        "message": f"Circuit breaker active: {circuit.get('reason')}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

            for name, p in portfolios.items():
                if p.get("pnl", 0) < ALERT_THRESHOLDS["pnl_drop_1h"]:
                    alerts.append({
                        "type": "pnl_alert",
                        "severity": "warning",
                        "instance": name,
                        "message": f"{p['label']}: PnL ${p['pnl']:.2f} below threshold",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

                if p.get("open_positions", 0) > ALERT_THRESHOLDS["open_positions_max"]:
                    alerts.append({
                        "type": "position_alert",
                        "severity": "warning",
                        "instance": name,
                        "message": f"{p['label']}: {p['open_positions']} open positions",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

            for alert in alerts:
                await self.redis.client.xadd("alerts:critical", alert)
                logger.warning(f"ALERT: {alert['type']} - {alert['message']}")

        except Exception as e:
            logger.error(f"Error checking metrics: {e}")


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    logger = logging.getLogger("monitoring")
    monitor = MonitoringService()
    asyncio.run(monitor.run())
