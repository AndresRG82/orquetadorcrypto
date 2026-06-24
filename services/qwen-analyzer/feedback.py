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
from shared.models import QwenFeedback

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("qwen-feedback")


class FeedbackLoop:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.db: Database | None = None
        self.running = False

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        logger.info("Feedback loop initialized")

    async def evaluate_trade_result(self, trade_data: dict) -> Optional[QwenFeedback]:
        signal_id = trade_data.get("signal_id", "")
        pnl = float(trade_data.get("pnl_usd", 0))
        confidence = float(trade_data.get("confidence", 0.5))

        analysis_correct = False
        if trade_data.get("side") == "buy":
            analysis_correct = pnl > 0
        elif trade_data.get("side") == "sell":
            analysis_correct = pnl > 0

        trade_result = "profit" if pnl > 0 else "loss" if pnl < 0 else "breakeven"

        insight_parts = []
        if pnl > 0 and confidence > 0.7:
            insight_parts.append("High confidence correct call")
        elif pnl < 0 and confidence > 0.7:
            insight_parts.append("High confidence wrong call - review conditions")
        elif pnl > 0 and confidence < 0.5:
            insight_parts.append("Low confidence but profitable - consider raising threshold")

        symbol = trade_data.get("symbol", "unknown")
        strategy = trade_data.get("strategy", "unknown")
        insight_parts.append(f"{strategy} on {symbol}: {trade_result}")

        insights = "; ".join(insight_parts)

        feedback = QwenFeedback(
            signal_id=signal_id,
            trade_result=trade_result,
            pnl=pnl,
            analysis_correct=analysis_correct,
            prompt_version="v1.0",
            insights=insights,
        )

        await self.db.execute(
            """INSERT INTO qwen_feedback (time, signal_id, trade_result, pnl, analysis_correct, prompt_version, insights)
               VALUES ($1, $2, $3, $4, $5, $6, $7)""",
            datetime.now(timezone.utc), feedback.signal_id, feedback.trade_result,
            feedback.pnl, feedback.analysis_correct, feedback.prompt_version, feedback.insights,
        )

        logger.info(f"Feedback recorded: signal={signal_id} result={trade_result} pnl=${pnl:.2f} correct={analysis_correct}")
        return feedback

    async def run(self):
        self.running = True
        await self.initialize()
        group = "feedback-loop"
        consumer = "feedback-consumer-1"

        logger.info("Feedback loop running, listening for trade results")
        while self.running:
            try:
                messages = await self.redis.read_stream(
                    settings.STREAM_TRADE_RESULTS, group, consumer, count=10, block=5000,
                )
                for msg_id, data in messages:
                    await self.evaluate_trade_result(data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Feedback loop error: {e}")
                await asyncio.sleep(5)


async def main():
    loop = FeedbackLoop()
    await loop.run()


if __name__ == "__main__":
    asyncio.run(main())