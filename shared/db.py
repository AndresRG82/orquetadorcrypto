import asyncpg
import logging
from contextlib import asynccontextmanager
from shared.config import settings

logger = logging.getLogger(__name__)


class Database:
    _instance = None
    _pool: asyncpg.Pool | None = None

    def __init__(self):
        self._pool = None

    @classmethod
    async def get_instance(cls) -> "Database":
        if cls._instance is None:
            cls._instance = cls()
        if cls._instance._pool is None:
            await cls._instance.connect()
        return cls._instance

    async def connect(self):
        self._pool = await asyncpg.create_pool(
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            database=settings.DB_NAME,
            user=settings.DB_USER,
            password=settings.DB_PASSWORD,
            min_size=2,
            max_size=10,
        )
        logger.info(f"Connected to TimescaleDB at {settings.DB_HOST}:{settings.DB_PORT}")

    async def close(self):
        if self._pool:
            await self._pool.close()

    @asynccontextmanager
    async def acquire(self):
        async with self._pool.acquire() as conn:
            yield conn

    async def execute(self, query: str, *args):
        async with self.acquire() as conn:
            return await conn.execute(query, *args)

    async def execute_many(self, query: str, args_list: list[tuple]):
        async with self.acquire() as conn:
            await conn.executemany(query, args_list)

    async def fetch(self, query: str, *args) -> list[asyncpg.Record]:
        async with self.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args) -> asyncpg.Record | None:
        async with self.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def fetchval(self, query: str, *args):
        async with self.acquire() as conn:
            return await conn.fetchval(query, *args)