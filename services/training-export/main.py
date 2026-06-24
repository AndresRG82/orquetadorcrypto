import asyncio
import json
import logging
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("training-export")

OUTPUT_DIR = "/app/data/training"
EXPORT_INTERVAL_HOURS = 24
MIN_EXAMPLES = 10


class TrainingExporter:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.db: Database | None = None
        self.running = False

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        logger.info("Training Exporter initialized")

    async def export_qwen_feedback(self) -> list[dict]:
        rows = await self.db.fetch(
            "SELECT signal_id, trade_result, pnl, analysis_correct, prompt_version, insights, time "
            "FROM qwen_feedback WHERE time > NOW() - INTERVAL '30 days' ORDER BY time ASC"
        )
        examples = []
        for r in rows:
            outcome = "correct" if r.get("analysis_correct") else "incorrect"
            instruction = f"Evaluate a trading signal. Result: {r.get('trade_result', 'unknown')}"
            output_parts = [f"Signal was {outcome}."]
            if r.get("insights"):
                output_parts.append(f"Insight: {r['insights']}")
            if r.get("pnl") is not None:
                output_parts.append(f"PnL: ${float(r['pnl']):.2f}")
            if r.get("trade_result"):
                output_parts.append(f"Result: {r['trade_result']}")
            examples.append({
                "instruction": instruction,
                "input": "",
                "output": ". ".join(output_parts),
                "source": "qwen_feedback",
                "strategy": "",
                "symbol": "",
            })
        return examples

    async def export_closed_trades(self) -> list[dict]:
        rows = await self.db.fetch(
            "SELECT strategy, symbol, side, entry_price, exit_price, quantity, "
            "pnl_usd, stop_loss, take_profit, time, status "
            "FROM trades WHERE status = 'closed' AND time > NOW() - INTERVAL '30 days' "
            "ORDER BY time ASC"
        )
        examples = []
        for r in rows:
            pnl = float(r["pnl_usd"]) if r.get("pnl_usd") else 0
            outcome = "profitable" if pnl > 0 else "losing"
            entry = float(r["entry_price"]) if r.get("entry_price") else 0
            exit_price = float(r["exit_price"]) if r.get("exit_price") else 0
            pnl_pct = ((exit_price - entry) / entry * 100) if entry > 0 and r["side"] == "buy" else 0
            instruction = (
                f"Evaluate a {r['strategy']} trade on {r['symbol']}: "
                f"{r['side']} entry=${entry:.2f} exit=${exit_price:.2f} "
                f"PnL=${pnl:.2f}"
            )
            output_parts = [f"This was a {outcome} trade."]
            if r.get("stop_loss"):
                output_parts.append(f"Stop was at ${float(r['stop_loss']):.2f}")
            if r.get("take_profit"):
                output_parts.append(f"Target was at ${float(r['take_profit']):.2f}")
            examples.append({
                "instruction": instruction,
                "input": "",
                "output": ". ".join(output_parts),
                "source": "trade_result",
                "strategy": r.get("strategy", ""),
                "symbol": r.get("symbol", ""),
            })
        return examples

    async def export_stop_loss_tracker(self) -> list[dict]:
        try:
            tracked_json = await self.redis.client.get("stop_loss_tracker:tracked")
            if not tracked_json:
                return []
            tracked = json.loads(tracked_json)
        except Exception:
            return []

        examples = []
        for track_id, track in tracked.items():
            if track.get("would_have_been_profitable") is None:
                continue

            symbol = track.get("symbol", "")
            strategy = track.get("strategy", "")
            side = track.get("side", "")
            entry_price = track.get("entry_price", 0)
            stop_price = track.get("price_at_stop", 0)
            final_price = track.get("final_price", 0)
            would_profit = track.get("would_have_been_profitable", False)
            max_favorable = track.get("max_favorable", 0)
            max_adverse = track.get("max_adverse", 0)

            if would_profit and max_favorable > 0.015:
                correct_action = side
                quality = "high"
            elif max_adverse > 0.02:
                correct_action = "hold"
                quality = "negative"
            else:
                correct_action = "hold"
                quality = "neutral"

            instruction = (
                f"Symbol: {symbol}\n"
                f"Strategy: {strategy}\n"
                f"Action: {side} at ${entry_price:.4f}\n"
                f"Stop triggered at ${stop_price:.4f}\n"
                f"Price after 30min: ${final_price:.4f}\n"
                f"Max favorable move: ${max_favorable:.4f}\n"
                f"Max adverse move: ${max_adverse:.4f}"
            )

            output = f"Action: {correct_action}. Quality: {quality}."
            if would_profit:
                output += f" The signal was correct - price moved ${max_favorable:.4f} in predicted direction."
            else:
                output += f" The signal was incorrect - price moved ${max_adverse:.4f} against prediction."

            examples.append({
                "instruction": instruction,
                "input": "",
                "output": output,
                "source": "stop_loss_tracker",
                "strategy": strategy,
                "symbol": symbol,
            })

        return examples

    async def export_all(self):
        logger.info("Starting training data export...")
        feedback_examples = await self.export_qwen_feedback()
        trade_examples = await self.export_closed_trades()
        tracker_examples = await self.export_stop_loss_tracker()
        all_examples = feedback_examples + trade_examples + tracker_examples

        if len(all_examples) < MIN_EXAMPLES:
            logger.info(f"Only {len(all_examples)} examples (need {MIN_EXAMPLES}), skipping export")
            return

        profitable = [e for e in all_examples if "profitable" in e.get("output", "") or "correct" in e.get("output", "").lower()]
        losing = [e for e in all_examples if "losing" in e.get("output", "") or "incorrect" in e.get("output", "").lower()]
        logger.info(f"Exported {len(all_examples)} examples ({len(profitable)} profitable, {len(losing)} losing)")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(OUTPUT_DIR, f"training_data_{timestamp}.json")
        with open(filepath, "w") as f:
            json.dump(all_examples, f, indent=2, default=str)
        logger.info(f"Saved {len(all_examples)} examples to {filepath}")

        latest_path = os.path.join(OUTPUT_DIR, "latest.json")
        with open(latest_path, "w") as f:
            json.dump(all_examples, f, indent=2, default=str)

        stats = {
            "total": len(all_examples),
            "feedback": len(feedback_examples),
            "trades": len(trade_examples),
            "tracker": len(tracker_examples),
            "profitable": len(profitable),
            "losing": len(losing),
            "strategies": list(set(e["strategy"] for e in all_examples if e["strategy"])),
            "symbols": list(set(e["symbol"] for e in all_examples if e["symbol"])),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self.redis.set_json("training:export_stats", stats)
        logger.info(f"Export stats: {stats}")

    async def run(self):
        self.running = True
        await self.initialize()
        logger.info("Training Exporter running (periodic export every 24h)")
        while self.running:
            try:
                await self.export_all()
                await asyncio.sleep(EXPORT_INTERVAL_HOURS * 3600)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Training export error: {e}")
                await asyncio.sleep(3600)


async def main():
    exporter = TrainingExporter()
    await exporter.run()


if __name__ == "__main__":
    asyncio.run(main())
