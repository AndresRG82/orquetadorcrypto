import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("freqtrade-bridge")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "30"))

FREQTRADE_INSTANCES = [
    {
        "name": "freqtrade-meanrev",
        "url": os.getenv("FREQTRADE_MEANREV_URL", "http://freqtrade-meanrev:8080"),
        "strategy": "MeanReversion",
        "config_key": "strategy:params:scalping",
    },
    {
        "name": "freqtrade-lowfreq",
        "url": os.getenv("FREQTRADE_LOWFREQ_URL", "http://freqtrade-lowfreq:8080"),
        "strategy": "LowFrequency",
        "config_key": "strategy:params:scalping",
    },
    {
        "name": "freqtrade-swing",
        "url": os.getenv("FREQTRADE_SWING_URL", "http://freqtrade-swing:8080"),
        "strategy": "SwingStrategy",
        "config_key": "strategy:params:swing",
    },
]


class FreqtradeBridge:
    def __init__(self):
        self.redis = None
        self.auth = httpx.BasicAuth("trader", "trader123")

    async def connect_redis(self):
        import redis.asyncio as aioredis
        self.redis = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        await self.redis.ping()
        logger.info("Connected to Redis")

    async def fetch_json(self, client: httpx.AsyncClient, url: str, endpoint: str) -> dict:
        try:
            resp = await client.get(
                f"{url}/api/v1/{endpoint}",
                auth=self.auth,
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.debug(f"Failed to fetch {url}/api/v1/{endpoint}: {e}")
            return {}

    def compute_pnl(self, balance: dict) -> float:
        try:
            total = float(balance.get("total", 0))
            initial = float(balance.get("initial_capital", 1000))
            return total - initial
        except (TypeError, ValueError):
            return 0.0

    def build_portfolio_snapshot(self, instance: dict, status: dict, balance: dict,
                                  trades: list) -> dict:
        total_usd = 0
        positions = []
        try:
            for coin in balance.get("currencies", []):
                est_btc = float(coin.get("est_btc", 0))
                total_usd += est_btc
            total_usd += float(balance.get("total", 0))
        except (TypeError, ValueError):
            total_usd = 0

        for t in trades[:20]:
            try:
                positions.append({
                    "symbol": t.get("pair", ""),
                    "side": "buy" if t.get("is_short") is False else "sell",
                    "quantity": float(t.get("amount", 0)),
                    "entry_price": float(t.get("open_rate", 0)),
                    "quantity_usd": float(t.get("stake_amount", 0)),
                    "strategy": instance["strategy"],
                    "order_id": t.get("trade_id", str(uuid.uuid4())),
                    "opened_at": t.get("open_date", datetime.now(timezone.utc).isoformat()),
                })
            except (TypeError, ValueError):
                continue

        pnl = self.compute_pnl(balance)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total_value_usd": total_usd,
            "cash_usd": float(balance.get("total", 0)),
            "positions": positions,
            "unrealized_pnl_usd": pnl,
            "strategy": instance["strategy"],
            "instance": instance["name"],
        }

    def build_trade_result(self, t: dict, instance: dict) -> dict:
        is_closed = t.get("close_date") is not None
        pnl = float(t.get("profit_ratio", 0)) * float(t.get("stake_amount", 0))
        return {
            "order_id": str(t.get("trade_id", uuid.uuid4())),
            "signal_id": f"freqtrade-{t.get('trade_id', '')}",
            "symbol": t.get("pair", ""),
            "side": "sell" if is_closed else "buy",
            "entry_price": float(t.get("open_rate", 0)),
            "exit_price": float(t.get("close_rate", 0)) if is_closed else 0,
            "quantity": float(t.get("amount", 0)),
            "quantity_usd": float(t.get("stake_amount", 0)),
            "fee_usd": float(t.get("fee", {}).get("cost", 0)) if isinstance(t.get("fee"), dict) else 0,
            "pnl_usd": pnl,
            "status": "closed" if is_closed else "open",
            "strategy": instance["strategy"],
            "confidence": 1.0,
            "reasoning": f"Freqtrade {instance['strategy']}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stop_loss": float(t.get("stop_loss", 0)) or None,
            "take_profit": float(t.get("take_profit", 0)) or None,
        }

    async def poll_instance(self, client: httpx.AsyncClient, instance: dict):
        name = instance["name"]
        url = instance["url"]

        status = await self.fetch_json(client, url, "status")
        if not status:
            return

        balance = await self.fetch_json(client, url, "balance")
        trades_data = await self.fetch_json(client, url, "trades")
        trades = trades_data.get("trades", trades_data if isinstance(trades_data, list) else [])
        profit = await self.fetch_json(client, url, "profit")

        snapshot = self.build_portfolio_snapshot(instance, status, balance, trades)
        state_key = f"paper_trading:{name}"
        stats_key = f"portfolio:stats:{name}"

        await self.redis.set(state_key, json.dumps(snapshot))
        await self.redis.set(stats_key, json.dumps({
            "instance": name,
            "strategy": instance["strategy"],
            "total_value": snapshot["total_value_usd"],
            "pnl": snapshot["unrealized_pnl_usd"],
            "positions": len(snapshot["positions"]),
            "updated_at": snapshot["timestamp"],
        }))

        for t in trades:
            if t.get("close_date"):
                result = self.build_trade_result(t, instance)
                await self.redis.publish("trade:results", json.dumps(result))

        logger.info(
            f"{name}: value=${snapshot['total_value_usd']:.2f}, "
            f"pnl=${snapshot['unrealized_pnl_usd']:.2f}, "
            f"positions={len(snapshot['positions'])}"
        )

    async def run_hyperopt_monitor(self):
        while True:
            try:
                for instance in FREQTRADE_INSTANCES:
                    key = f"freqtrade:hyperopt:{instance['name']}"
                    result = await self.redis.get(key)
                    if result:
                        try:
                            params = json.loads(result)
                            config_key = instance["config_key"]
                            await self.redis.set(config_key, json.dumps(params))
                            await self.redis.delete(key)
                            logger.info(f"Applied hyperopt params to {config_key}: {params}")
                        except json.JSONDecodeError:
                            await self.redis.delete(key)
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Hyperopt monitor error: {e}")
                await asyncio.sleep(60)

    async def run(self):
        await self.connect_redis()

        logger.info(f"Freqtrade Bridge running (poll={POLL_INTERVAL}s, {len(FREQTRADE_INSTANCES)} instances)")
        hyperopt_task = asyncio.create_task(self.run_hyperopt_monitor())

        async with httpx.AsyncClient() as client:
            while True:
                try:
                    tasks = [self.poll_instance(client, inst) for inst in FREQTRADE_INSTANCES]
                    await asyncio.gather(*tasks, return_exceptions=True)
                except Exception as e:
                    logger.error(f"Poll error: {e}")
                await asyncio.sleep(POLL_INTERVAL)


async def main():
    bridge = FreqtradeBridge()
    try:
        await bridge.run()
    except KeyboardInterrupt:
        logger.info("Shutting down")


if __name__ == "__main__":
    asyncio.run(main())
