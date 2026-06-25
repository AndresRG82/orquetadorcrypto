import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("swarm-coordinator")


class SwarmCoordinator:
    def __init__(self):
        self.redis: Optional[RedisClient] = None
        self.runtime = None
        self.running = False

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        from runtime import OllamaRuntime
        self.runtime = OllamaRuntime(
            base_url=settings.OLLAMA_HOST,
            primary_model="gemma3:4b",
            fallback_model="qwen2.5:3b",
        )
        logger.info("SwarmCoordinator initialized")

    async def gather_market_data(self) -> dict:
        data = {}
        stats = await self.redis.get_json("portfolio:stats") or {}
        risk_params = await self.redis.get_json("risk:params") or {}
        sentiment = await self.redis.get_json("sentiment:current") or {}
        signals = await self.redis.get_json("strategy:latest_signals") or []
        metrics = await self.redis.get_json("strategy:metrics") or {}
        backtest = await self.redis.get_json("backtest:latest") or {}

        market_lines = []
        if stats:
            market_lines.append(f"Portfolio: ${stats.get('total_value', 0):.0f} | "
                                f"PnL: ${stats.get('total_pnl', 0):.0f} ({stats.get('total_pnl_pct', 0):.1f}%) | "
                                f"Trades: {stats.get('total_trades', 0)} | WR: {stats.get('win_rate', 0):.1f}%")

        return {
            "market_data": "\n".join(market_lines) if market_lines else "Sin datos de mercado",
            "portfolio_data": json.dumps({
                "total_value": stats.get("total_value"),
                "cash": stats.get("cash"),
                "total_pnl": stats.get("total_pnl"),
                "total_pnl_pct": stats.get("total_pnl_pct"),
                "open_positions": stats.get("open_positions"),
                "total_trades": stats.get("total_trades"),
                "win_rate": stats.get("win_rate"),
            }, indent=2),
            "risk_params": json.dumps(risk_params, indent=2),
            "recent_signals": json.dumps((signals or [])[-10:], indent=2) if signals else "Sin señales recientes",
            "strategy_metrics": json.dumps(metrics, indent=2) if metrics else "Sin métricas",
            "sentiment_data": json.dumps(sentiment, indent=2) if sentiment else "Sin datos de sentimiento",
        }

    async def run_cycle(self):
        if self.runtime is None:
            return

        from agents import (
            MARKET_ANALYST_PROMPT, RISK_MANAGER_PROMPT,
            STRATEGY_CRITIC_PROMPT, SENTIMENT_ANALYST_PROMPT,
            COORDINATOR_PROMPT,
        )

        context = await self.gather_market_data()

        agent_tasks = {
            "market_analyst": self.runtime.query_agent(MARKET_ANALYST_PROMPT, context, 0.4),
            "risk_manager": self.runtime.query_agent(RISK_MANAGER_PROMPT, context, 0.3),
            "strategy_critic": self.runtime.query_agent(STRATEGY_CRITIC_PROMPT, context, 0.3),
            "sentiment_analyst": self.runtime.query_agent(SENTIMENT_ANALYST_PROMPT, context, 0.4),
        }

        results = {}
        for name, task in agent_tasks.items():
            try:
                results[name] = await task
                logger.info("Agent %s responded (%d chars)", name, len(results[name]))
            except Exception as e:
                results[name] = ""
                logger.warning("Agent %s failed: %s", name, e)

        coordinator_context = {
            **context,
            "market_analyst_output": results.get("market_analyst", "N/A"),
            "risk_manager_output": results.get("risk_manager", "N/A"),
            "strategy_critic_output": results.get("strategy_critic", "N/A"),
            "sentiment_analyst_output": results.get("sentiment_analyst", "N/A"),
        }

        decision = await self.runtime.query_coordinator(COORDINATOR_PROMPT, coordinator_context)
        decision["timestamp"] = datetime.now(timezone.utc).isoformat()
        decision["agent_outputs"] = results

        await self.redis.set_json("swarm:latest", decision)
        logger.info("Swarm cycle complete: outlook=%s risk=%s kelly=%.2f",
                    decision.get("market_outlook"),
                    decision.get("risk_adjustment"),
                    decision.get("kelly_fraction_suggested", 0))

        if decision.get("risk_adjustment") == "reduce":
            risk = await self.redis.get_json("risk:params") or {}
            current_kelly = risk.get("kelly_fraction", 0.15)
            new_kelly = max(0.05, current_kelly * 0.8)
            risk["kelly_fraction"] = new_kelly
            await self.redis.set_json("risk:params", risk)
            logger.info("Risk adjustment: kelly reduced to %.2f", new_kelly)

        return decision

    async def run(self):
        self.running = True
        await self.initialize()
        logger.info("SwarmCoordinator running (every 30 min)")
        while self.running:
            try:
                await self.run_cycle()
                await asyncio.sleep(1800)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Swarm cycle error: %s", e)
                await asyncio.sleep(300)

        if self.runtime:
            await self.runtime.close()


async def main():
    coordinator = SwarmCoordinator()
    await coordinator.run()


if __name__ == "__main__":
    asyncio.run(main())
