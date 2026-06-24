import redis.asyncio as aioredis
import json
import logging
from typing import AsyncGenerator
from shared.config import settings

logger = logging.getLogger(__name__)


class RedisClient:
    _instance = None

    def __init__(self):
        self.client: aioredis.Redis | None = None

    @classmethod
    async def get_instance(cls) -> "RedisClient":
        if cls._instance is None:
            cls._instance = cls()
            await cls._instance.connect()
        if cls._instance.client is None:
            await cls._instance.connect()
        return cls._instance

    async def connect(self):
        self.client = aioredis.from_url(
            settings.REDIS_URL, decode_responses=True
        )
        logger.info(f"Connected to Redis at {settings.REDIS_URL}")

    async def close(self):
        if self.client:
            await self.client.close()

    async def publish(self, stream: str, data: dict) -> str:
        serialized = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in data.items()}
        msg_id = await self.client.xadd(stream, serialized)
        return msg_id

    async def read_stream(self, stream: str, group: str, consumer: str, count: int = 10, block: int = 5000) -> list[tuple[str, dict]]:
        try:
            try:
                await self.client.xgroup_create(stream, group, id="0", mkstream=True)
            except Exception:
                pass
            messages = await self.client.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={stream: ">"},
                count=count,
                block=block,
            )
            results = []
            if messages:
                for stream_name, stream_messages in messages:
                    for msg_id, msg_data in stream_messages:
                        parsed = {}
                        for k, v in msg_data.items():
                            try:
                                val = json.loads(v)
                                parsed[k] = None if val is None else val
                            except (json.JSONDecodeError, TypeError):
                                parsed[k] = None if v == "None" else v
                        results.append((msg_id, parsed))
                        await self.client.xack(stream, group, msg_id)
            return results
        except Exception as e:
            logger.error(f"Error reading stream {stream}: {e}")
            return []

    async def set(self, key: str, value: str, ex: int | None = None):
        await self.client.set(key, value, ex=ex)

    async def get(self, key: str) -> str | None:
        return await self.client.get(key)

    async def get_json(self, key: str) -> dict | None:
        val = await self.get(key)
        if val:
            return json.loads(val)
        return None

    async def set_json(self, key: str, value: dict, ex: int | None = None):
        await self.set(key, json.dumps(value), ex=ex)