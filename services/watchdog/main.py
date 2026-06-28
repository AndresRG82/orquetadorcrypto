import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import httpx
import redis.asyncio as aioredis

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("watchdog")

REDIS_URL = "redis://redis:6379"
DOCKER_SOCKET = "/var/run/docker.sock"

CHECK_INTERVAL = 120
IDLE_WARN_MS = 300_000
IDLE_RESTART_MS = 600_000
STREAM_FRESHNESS_WARN_S = 300
STREAM_FRESHNESS_RESTART_S = 600

CONSUMER_MAP = {
    "market:data": {
        "producer": "crypto-trader-market-scanner-1",
        "consumers": {
            "orchestrator-market": "crypto-trader-orchestrator-1",
            "paper-trading-market-main": "crypto-trader-paper-trading-1",
            "stop-loss-market": "crypto-trader-stop-loss-1",
        },
    },
    "strategy:signals": {
        "producers": {
            "strategy-scalping": "crypto-trader-strategy-scalping-1",
            "strategy-swing": "crypto-trader-strategy-swing-1",
            "strategy-arbitrage": "crypto-trader-strategy-arbitrage-1",
        },
        "consumers": {
            "risk-manager-signals": "crypto-trader-risk-manager-1",
            "orchestrator-signals": "crypto-trader-orchestrator-1",
        },
    },
    "risk:approved": {
        "producer": "crypto-trader-risk-manager-1",
        "consumers": {
            "orchestrator-approved": "crypto-trader-orchestrator-1",
        },
    },
    "trade:orders": {
        "producer": "crypto-trader-risk-manager-1",
        "consumers": {
            "paper-trading-orders-main": "crypto-trader-paper-trading-1",
        },
    },
    "trade:results": {
        "producer": "crypto-trader-paper-trading-1",
        "consumers": {
            "orchestrator-results": "crypto-trader-orchestrator-1",
            "risk-manager-results": "crypto-trader-risk-manager-1",
            "stop-loss-results": "crypto-trader-stop-loss-1",
        },
    },
}

RESTARTED: dict[str, float] = {}
RESTART_COOLDOWN = 600


class Watchdog:
    def __init__(self):
        self.redis: aioredis.Redis | None = None
        self.docker: httpx.AsyncClient | None = None
        self.restart_log: list[dict] = []

    async def initialize(self):
        self.redis = aioredis.from_url(REDIS_URL, decode_responses=True)
        transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET)
        self.docker = httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=30)
        logger.info("Watchdog initialized")

    async def get_consumer_info(self, stream: str, group: str) -> list[dict]:
        try:
            info = await self.redis.xinfo_consumers(stream, group)
            return info
        except Exception:
            return []

    async def get_stream_length(self, stream: str) -> int:
        try:
            return await self.redis.xlen(stream)
        except Exception:
            return 0

    async def get_last_message_time(self, stream: str) -> float:
        try:
            result = await self.redis.xrevrange(stream, count=1)
            if result:
                msg_id = result[0][0]
                ts_ms = int(msg_id.split("-")[0])
                return ts_ms / 1000.0
        except Exception:
            pass
        return 0

    async def restart_container(self, container_name: str, reason: str) -> bool:
        now = time.time()
        last_restart = RESTARTED.get(container_name, 0)
        if now - last_restart < RESTART_COOLDOWN:
            logger.warning(f"SKIP restart {container_name}: cooldown ({int(now - last_restart)}s since last)")
            return False

        try:
            resp = await self.docker.post(f"/containers/{container_name}/restart", timeout=60)
            if resp.status_code == 204:
                RESTARTED[container_name] = now
                entry = {
                    "time": datetime.now(timezone.utc).isoformat(),
                    "container": container_name,
                    "reason": reason,
                }
                self.restart_log.append(entry)
                if len(self.restart_log) > 100:
                    self.restart_log = self.restart_log[-50:]
                await self.redis.set("watchdog:last_restart", json.dumps(entry), ex=3600)
                logger.info(f"RESTARTED {container_name}: {reason}")
                return True
            else:
                logger.error(f"Failed to restart {container_name}: HTTP {resp.status_code}")
                return False
        except Exception as e:
            logger.error(f"Error restarting {container_name}: {e}")
            return False

    async def check_consumers(self):
        for stream, config in CONSUMER_MAP.items():
            consumers = config.get("consumers", {})
            for group_name, container_name in consumers.items():
                infos = await self.get_consumer_info(stream, group_name)
                for info in infos:
                    idle_ms = info.get("idle", 0)
                    pending = info.get("pending", 0)

                    if idle_ms > IDLE_RESTART_MS:
                        reason = f"Consumer {group_name} idle {idle_ms/1000:.0f}s (>{IDLE_RESTART_MS/1000:.0f}s), pending={pending}"
                        await self.restart_container(container_name, reason)
                    elif idle_ms > IDLE_WARN_MS:
                        logger.warning(f"WARN: Consumer {group_name} idle {idle_ms/1000:.0f}s (>{IDLE_WARN_MS/1000:.0f}s)")

    async def check_streams(self):
        for stream, config in CONSUMER_MAP.items():
            last_msg_time = await self.get_last_message_time(stream)
            if last_msg_time <= 0:
                continue

            age_s = time.time() - last_msg_time

            producer = config.get("producer")
            if isinstance(producer, str) and age_s > STREAM_FRESHNESS_RESTART_S:
                container = CONSUMER_MAP[stream].get("producer")
                if container:
                    reason = f"Stream {stream} stale {age_s:.0f}s (>{STREAM_FRESHNESS_RESTART_S}s)"
                    await self.restart_container(container, reason)
            elif isinstance(producer, str) and age_s > STREAM_FRESHNESS_WARN_S:
                logger.warning(f"WARN: Stream {stream} stale {age_s:.0f}s")

            producers = config.get("producers", {})
            if producers and age_s > STREAM_FRESHNESS_RESTART_S:
                for strat_name, container_name in producers.items():
                    reason = f"Stream {stream} stale {age_s:.0f}s (>{STREAM_FRESHNESS_RESTART_S}s), possibly {strat_name}"
                    await self.restart_container(container_name, reason)
                    break

    async def check_docker_health(self):
        try:
            resp = await self.docker.get("/containers/json")
            if resp.status_code != 200:
                logger.error(f"Docker API error: {resp.status_code}")
                return

            containers = resp.json()
            for c in containers:
                name = c.get("Names", [""])[0].lstrip("/")
                state = c.get("State", "")
                status = c.get("Status", "")

                if state == "exited" and "crypto-trader" in name:
                    reason = f"Container {name} exited (state={state}, status={status})"
                    await self.restart_container(name, reason)

        except Exception as e:
            logger.error(f"Error checking Docker health: {e}")

    async def save_status(self):
        status = {
            "last_check": datetime.now(timezone.utc).isoformat(),
            "restarts": self.restart_log[-10:],
            "total_restarts": len(self.restart_log),
        }
        await self.redis.set("watchdog:status", json.dumps(status), ex=600)

    async def run(self):
        await self.initialize()
        logger.info(f"Watchdog running: check={CHECK_INTERVAL}s, warn_idle={IDLE_WARN_MS/1000:.0f}s, restart_idle={IDLE_RESTART_MS/1000:.0f}s")

        while True:
            try:
                await self.redis.set(f"service:heartbeat:watchdog", json.dumps({"last_seen": datetime.now(timezone.utc).isoformat()}))
                logger.info("--- Check cycle ---")
                await self.check_consumers()
                await self.check_streams()
                await self.check_docker_health()
                await self.save_status()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Watchdog error: {e}")

            await asyncio.sleep(CHECK_INTERVAL)


async def main():
    wd = Watchdog()
    await wd.run()


if __name__ == "__main__":
    asyncio.run(main())
