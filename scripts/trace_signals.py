#!/usr/bin/env python3
"""Verify signal_id traceability end-to-end.

Usage:
  python scripts/trace_signals.py                    # check last 100 signal_ids
  python scripts/trace_signals.py --hours 24         # search back N hours
  python scripts/trace_signals.py --signal <uuid>    # trace one specific signal
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.redis_client import RedisClient
from shared.config import settings
from shared.db import Database as DB

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("trace-signals")


async def get_signal_ids_from_redis(redis: RedisClient, limit: int = 100) -> list[str]:
    signals = await redis.get_json("strategy:latest_signals") or []
    return [s["signal_id"] for s in signals if s.get("signal_id")][:limit]


async def get_signal_ids_from_db(db, hours: int) -> list[str]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = await db.fetch(
        "SELECT signal_id FROM signals WHERE time >= $1 ORDER BY time DESC LIMIT 100",
        since,
    )
    return [r["signal_id"] for r in rows]


async def check_signal_propagation(signal_id: str, db) -> dict:
    result = {
        "signal_id": signal_id,
        "in_signals_table": False,
        "signal_approved": None,
        "in_trades_table": False,
        "in_qwen_feedback": False,
        "trade_pnl": None,
        "errors": [],
    }

    signal = await db.fetchrow(
        "SELECT approved FROM signals WHERE signal_id = $1", signal_id
    )
    if signal:
        result["in_signals_table"] = True
        result["signal_approved"] = signal["approved"]
    else:
        result["errors"].append("signal_id not found in signals table")

    trade = await db.fetchrow(
        "SELECT pnl_usd, status FROM trades WHERE signal_id = $1 ORDER BY time DESC LIMIT 1",
        signal_id,
    )
    if trade:
        result["in_trades_table"] = True
        result["trade_pnl"] = trade["pnl_usd"]
    else:
        result["errors"].append("signal_id not found in trades table")

    fb = await db.fetchrow(
        "SELECT 1 FROM qwen_feedback WHERE signal_id = $1", signal_id
    )
    if fb:
        result["in_qwen_feedback"] = True

    return result


async def main():
    parser = argparse.ArgumentParser(description="Trace signal_id propagation")
    parser.add_argument("--hours", type=int, default=48, help="Search back N hours")
    parser.add_argument("--signal", type=str, help="Trace a specific signal_id")
    args = parser.parse_args()

    db = DB()
    await db.connect()
    redis = await RedisClient.get_instance()

    if args.signal:
        signal_ids = [args.signal]
    else:
        signal_ids = await get_signal_ids_from_db(db, args.hours)
        if not signal_ids:
            signal_ids = await get_signal_ids_from_redis(redis, 100)
        if not signal_ids:
            logger.warning("No signal_ids found. Is the system running?")
            return
        logger.info(f"Found {len(signal_ids)} signal_ids to check")

    total = len(signal_ids)
    found_signals = 0
    found_trades = 0
    found_feedback = 0
    approved = 0

    for sid in signal_ids:
        status = await check_signal_propagation(sid, db)
        if status["in_signals_table"]:
            found_signals += 1
            if status["signal_approved"]:
                approved += 1
        if status["in_trades_table"]:
            found_trades += 1
        if status["in_qwen_feedback"]:
            found_feedback += 1

        if status["errors"]:
            logger.warning(f"  {sid[:12]}... ERRORS: {'; '.join(status['errors'])}")
        else:
            pnl_str = f" PnL=${status['trade_pnl']:.2f}" if status["trade_pnl"] is not None else ""
            logger.info(
                f"  {sid[:12]}... OK approved={status['signal_approved']}{pnl_str}"
            )
        await asyncio.sleep(0)

    print()
    print("=" * 50)
    print(f"Total signal_ids checked:  {total}")
    print(f"Found in signals table:    {found_signals}/{total} ({100*found_signals/total:.0f}%)")
    print(f"  Approved by risk-manager: {approved}/{found_signals}")
    print(f"Found in trades table:     {found_trades}/{total} ({100*found_trades/total:.0f}%)")
    print(f"Found in qwen_feedback:    {found_feedback}/{total} ({100*found_feedback/total:.0f}%)")
    lost = total - found_trades
    if lost:
        print(f"  ⚠️  {lost} signal_ids never reached trades table")
    print("=" * 50)

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
