#!/usr/bin/env python3
"""Deterministic stream replay for the crypto-trader pipeline.

Modes:
  --record <file>    Record a Redis stream to a JSON file
  --replay <file>    Replay a recorded file to a Redis stream

Usage:
  python replay.py --record data.json --stream market:indicators --duration 120
  python replay.py --replay data.json --stream market:indicators --speed 10
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time as _time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shared.redis_client import RedisClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [replay] %(levelname)s: %(message)s")
logger = logging.getLogger("replay")


async def record(stream_name: str, output_path: str, duration_seconds: int = 3600):
    redis = await RedisClient.get_instance()
    logger.info(f"Recording {stream_name} to {output_path} for {duration_seconds}s...")
    records = []
    group = "replay-recorder"
    consumer = "recorder-1"
    try:
        await redis.redis.xgroup_create(stream_name, group, id="0", mkstream=True)
    except Exception:
        pass

    start = _time.time()
    while _time.time() - start < duration_seconds:
        try:
            msgs = await redis.read_stream(
                stream_name, group, consumer, count=100, block=2000,
            )
            for msg_id, data in msgs:
                entry = {"_stream_id": msg_id, "_recorded_at": datetime.now(timezone.utc).isoformat(), **data}
                records.append(entry)
            await asyncio.sleep(0.01)
        except Exception as e:
            logger.error(f"Record error: {e}")
            await asyncio.sleep(1)

    with open(output_path, "w") as f:
        json.dump(records, f, default=str, indent=2)
    logger.info(f"Recorded {len(records)} entries from {stream_name} to {output_path}")
    await redis.close()


async def replay(stream_name: str, input_path: str, speed: float = 1.0):
    redis = await RedisClient.get_instance()
    logger.info(f"Replaying {input_path} to {stream_name} at {speed}x speed...")

    with open(input_path) as f:
        records = json.load(f)

    if not records:
        logger.warning("Empty dataset, nothing to replay")
        return

    records.sort(key=lambda r: r.get("timestamp", ""))
    first_ts = records[0].get("timestamp", "")
    last_ts = records[-1].get("timestamp", "")
    logger.info(f"Dataset: {len(records)} entries, {first_ts[:19]} → {last_ts[:19]}")

    entries_by_ts: dict[str, list[dict]] = {}
    for r in records:
        ts = r.get("timestamp", "")
        entries_by_ts.setdefault(ts, []).append(r)

    timestamps = sorted(entries_by_ts.keys())

    for i, ts in enumerate(timestamps):
        batch = entries_by_ts[ts]
        for entry in batch:
            payload = {k: v for k, v in entry.items() if not k.startswith("_")}
            payload["_replay"] = True
            payload["_replay_seq"] = i
            await redis.publish(stream_name, payload)

        if i < len(timestamps) - 1 and speed < 50:
            ts_next = timestamps[i + 1]
            try:
                dt_current = datetime.fromisoformat(ts)
                dt_next = datetime.fromisoformat(ts_next)
                gap = (dt_next - dt_current).total_seconds() / speed
                if gap > 0:
                    await asyncio.sleep(min(gap, 0.5))
            except Exception:
                await asyncio.sleep(0.01)
        elif i < len(timestamps) - 1:
            await asyncio.sleep(0.001)

        if (i + 1) % 100 == 0:
            logger.info(f"Replayed {i + 1}/{len(timestamps)} timestamps ({len(batch)} entries)")

    logger.info(f"Replay complete: {len(timestamps)} timestamps, {len(records)} total entries")
    await redis.close()


def main():
    parser = argparse.ArgumentParser(description="Record/replay Redis streams for deterministic testing")
    parser.add_argument("--record", metavar="FILE", help="Record stream to FILE")
    parser.add_argument("--replay", metavar="FILE", help="Replay from FILE")
    parser.add_argument("--stream", default="market:indicators",
                        help="Redis stream name (default: market:indicators)")
    parser.add_argument("--duration", type=int, default=120, help="Recording duration in seconds")
    parser.add_argument("--speed", type=float, default=10.0, help="Replay speed multiplier")
    args = parser.parse_args()

    if not args.record and not args.replay:
        parser.print_help()
        return

    if args.record:
        asyncio.run(record(args.stream, args.record, args.duration))
    if args.replay:
        asyncio.run(replay(args.stream, args.replay, args.speed))


if __name__ == "__main__":
    main()
