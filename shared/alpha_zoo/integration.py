import time
import logging
from typing import Optional

import pandas as pd
import numpy as np

from shared.alpha_zoo.registry import get_default_registry
from shared.db import Database

logger = logging.getLogger(__name__)

ALPHA_PANEL_DAYS = 30
ALPHA_REFRESH_SECONDS = 300
ALPHA_PANEL_MIN_ROWS = 100


class AlphaIntegration:
    def __init__(self):
        self._registry = get_default_registry()
        self._caches: dict[str, dict[str, float]] = {}
        self._cache_times: dict[str, float] = {}
        self._cache_ttl: float = ALPHA_REFRESH_SECONDS
        self._db: Optional[Database] = None
        self.db_ready = False

    async def ensure_db(self):
        if not self.db_ready:
            self._db = await Database.get_instance()
            self.db_ready = True
        return self._db

    async def build_panel(
        self,
        timeframe: str,
        lookback_days: int = ALPHA_PANEL_DAYS,
    ) -> Optional[pd.DataFrame]:
        db = await self.ensure_db()
        rows = await db.fetch(
            """
            SELECT time, symbol, open, high, low, close, volume
            FROM candles
            WHERE timeframe = $1
              AND time >= NOW() - INTERVAL '1 day' * $2
            ORDER BY time, symbol
            """,
            timeframe,
            lookback_days,
        )
        if not rows:
            return None

        records = [dict(r) for r in rows]
        df = pd.DataFrame(records)
        if len(df) < ALPHA_PANEL_MIN_ROWS:
            return None

        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index(["symbol", "time"]).sort_index()

        required = ["open", "high", "low", "close", "volume"]
        for col in required:
            if col not in df.columns:
                return None
        return df[required + [c for c in df.columns if c not in required]]

    async def refresh_scores(self, timeframe: str) -> dict[str, float]:
        panel = await self.build_panel(timeframe)
        if panel is None:
            logger.warning("Alpha panel insufficient data for %s", timeframe)
            return {}

        symbols = panel.index.get_level_values(0).unique()
        alpha_ids = self._registry.list()
        factor_scores: dict[str, dict[str, float]] = {}

        for alpha_id in alpha_ids:
            try:
                series = self._registry.compute(alpha_id, panel)
                if series is None or not isinstance(series, pd.Series):
                    continue
                series = series.groupby(level=0).last()
                factor_scores[alpha_id] = series.to_dict()
            except Exception as e:
                logger.debug("Alpha %s failed: %s", alpha_id, e)

        if not factor_scores:
            return {}

        df = pd.DataFrame(factor_scores)
        df = df.replace([np.inf, -np.inf], np.nan)
        df = df.dropna(axis=1, how="all")
        if df.empty:
            return {}

        ranks = df.rank(pct=True)
        composite = ranks.mean(axis=1)
        composite = (composite - composite.mean()) / composite.std()
        composite = composite.clip(-3, 3)

        return composite.to_dict()

    async def ensure_scores(self, timeframe: str) -> dict[str, float]:
        now = time.time()
        last = self._cache_times.get(timeframe, 0.0)
        if now - last > self._cache_ttl or timeframe not in self._caches:
            try:
                scores = await self.refresh_scores(timeframe)
                self._caches[timeframe] = scores
                self._cache_times[timeframe] = now
                n = len(scores)
                if n > 0:
                    logger.info("Alpha scores refreshed (%d symbols) for %s", n, timeframe)
            except Exception as e:
                logger.warning("Alpha refresh failed for %s: %s", timeframe, e)
        return self._caches.get(timeframe, {})

    def get_alpha_score(self, symbol: str) -> float:
        for cache in self._caches.values():
            if symbol in cache:
                return cache[symbol]
        return 0.0

    def blend(
        self,
        tech_score: int,
        symbol: str,
        weight: float = 0.3,
    ) -> tuple[int, str]:
        alpha = self.get_alpha_score(symbol)
        if abs(alpha) < 0.01:
            return tech_score, ""
        alpha_component = round(alpha * 3.0)
        blended = tech_score + alpha_component
        note = f"alpha={alpha:+.3f} comp={alpha_component:+d}"
        return blended, note

    def is_stale(self, timeframe: str) -> bool:
        now = time.time()
        last = self._cache_times.get(timeframe, 0.0)
        return now - last > self._cache_ttl
