#!/usr/bin/env python3
"""A/B Auto-promoter - promotes winning instances and deprecates losers."""

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("ab-promoter")

CHECK_INTERVAL = 24 * 3600  # 24 hours

INSTANCES = {
    "main": {"state": "paper_trading:state", "stats": "portfolio:stats"},
    "highconf": {"state": "paper_trading:highconf", "stats": "portfolio:stats:highconf"},
    "main-tf": {"state": "paper_trading:main-tf", "stats": "portfolio:stats:main-tf"},
    "conservative-tf": {"state": "paper_trading:conservative-tf", "stats": "portfolio:stats:conservative-tf"},
    "highconf-tf": {"state": "paper_trading:highconf-tf", "stats": "portfolio:stats:highconf-tf"},
    "multitf-tf": {"state": "paper_trading:multitf-tf", "stats": "portfolio:stats:multitf-tf"},
    "lowfreq-tf": {"state": "paper_trading:lowfreq-tf", "stats": "portfolio:stats:lowfreq-tf"},
    "sentiment-tf": {"state": "paper_trading:sentiment-tf", "stats": "portfolio:stats:sentiment-tf"},
}

PROMOTION_RULES = {
    "min_trades": 20,
    "min_wr": 0.40,
    "deprecation_wr": 0.35,
    "deprecation_hours": 48,
    "capital_boost_pct": 0.20,
    "capital_penalty_pct": 0.10,
}


class ABPromoter:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.running = False

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        logger.info("A/B Promoter initialized")

    async def run(self):
        await self.initialize()
        self.running = True
        logger.info("A/B Promoter running")
        while self.running:
            try:
                await self.evaluate_and_promote()
            except Exception as e:
                logger.error(f"Error in promotion cycle: {e}")
            await asyncio.sleep(CHECK_INTERVAL)

    async def evaluate_and_promote(self):
        rankings = []

        for name, cfg in INSTANCES.items():
            try:
                stats = await self.redis.get_json(cfg["stats"])
                if not stats:
                    continue

                trades = stats.get("total_trades", 0)
                if trades < PROMOTION_RULES["min_trades"]:
                    continue

                score = self.score_instance(stats)
                if score > -999:
                    rankings.append({
                        "instance": name,
                        "score": score,
                        "stats": stats,
                    })
            except Exception as e:
                logger.error(f"Error reading stats for {name}: {e}")

        if not rankings:
            logger.info("No eligible instances for promotion")
            return

        rankings.sort(key=lambda x: x["score"], reverse=True)

        winner = rankings[0]
        loser = rankings[-1]

        logger.info(f"Winner: {winner['instance']} (score={winner['score']:.3f})")
        logger.info(f"Loser: {loser['instance']} (score={loser['score']:.3f})")

        await self.promote_winner(winner)
        await self.deprecate_loser(loser)

    def score_instance(self, stats: dict) -> float:
        wr = stats.get("win_rate", 0)
        pnl = stats.get("total_pnl", 0)
        trades = stats.get("total_trades", 1)
        drawdown = abs(stats.get("max_drawdown", 0))

        if trades < PROMOTION_RULES["min_trades"]:
            return -999
        if wr < PROMOTION_RULES["deprecation_wr"]:
            return -999

        pnl_per_trade = pnl / trades
        sharpe_proxy = pnl_per_trade / (drawdown + 0.001)
        score = (wr * 0.4) + (sharpe_proxy * 0.4) + (min(pnl, 100) / 100 * 0.2)
        return score

    async def promote_winner(self, winner: dict):
        try:
            state_key = INSTANCES[winner["instance"]]["state"]
            state = await self.redis.get_json(state_key)
            if not state:
                return

            current_cash = state.get("cash", 1000)
            boost = current_cash * PROMOTION_RULES["capital_boost_pct"]
            new_cash = current_cash + boost

            state["cash"] = new_cash
            await self.redis.client.set(state_key, json.dumps(state))

            await self.redis.client.xadd("ab:promotions", {
                "winner": winner["instance"],
                "score": str(winner["score"]),
                "boost": str(boost),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            logger.info(f"Promoted {winner['instance']}: +${boost:.2f} cash")
        except Exception as e:
            logger.error(f"Error promoting winner: {e}")

    async def deprecate_loser(self, loser: dict):
        try:
            stats = loser["stats"]
            wr = stats.get("win_rate", 0)

            if wr >= PROMOTION_RULES["deprecation_wr"]:
                return

            state_key = INSTANCES[loser["instance"]]["state"]
            state = await self.redis.get_json(state_key)
            if not state:
                return

            current_cash = state.get("cash", 1000)
            penalty = current_cash * PROMOTION_RULES["capital_penalty_pct"]
            new_cash = current_cash - penalty

            state["cash"] = new_cash
            await self.redis.client.set(state_key, json.dumps(state))

            await self.redis.client.xadd("alerts:critical", {
                "type": "instance_deprecated",
                "instance": loser["instance"],
                "reason": f"WR {wr:.0%} < {PROMOTION_RULES['deprecation_wr']:.0%}",
                "penalty": str(penalty),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            logger.info(f"Deprecated {loser['instance']}: -${penalty:.2f} cash (WR={wr:.0%})")
        except Exception as e:
            logger.error(f"Error deprecating loser: {e}")


if __name__ == "__main__":
    promoter = ABPromoter()
    asyncio.run(promoter.run())
