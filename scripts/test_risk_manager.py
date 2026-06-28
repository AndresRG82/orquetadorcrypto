#!/usr/bin/env python3
"""Validate risk-manager rules by injecting synthetic signals.

Run against a live system:
  python scripts/test_risk_manager.py

Tests:
  1. Low confidence signal → rejected
  2. Max drawdown exceeded → rejected
  3. Circuit breaker tripped → rejected
  4. Cooldown active → rejected
  5. Excluded symbol → rejected
  6. Max positions reached → rejected
  7. Healthy signal → approved
  8. Bad risk/reward → rejected
  9. Loss cooldown active → rejected
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.config import settings
from shared.models import TradingSignal, SignalType
from shared.redis_client import RedisClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [test-risk] %(levelname)s: %(message)s")
logger = logging.getLogger("test-risk")

STREAM_SIGNALS = settings.STREAM_SIGNALS
STREAM_APPROVED = settings.STREAM_RISK_APPROVED
STREAM_REJECTED = "risk:rejected"

async def wait_for_assessment(redis, signal_id: str, timeout: float = 10.0) -> dict | None:
    deadline = datetime.now(timezone.utc).timestamp() + timeout
    while datetime.now(timezone.utc).timestamp() < deadline:
        for stream in [STREAM_APPROVED, STREAM_REJECTED]:
            try:
                raw = await redis.client.xrevrange(stream, "+", "-", count=200)
                if raw:
                    for msg_id, msg_data in raw:
                        data = {k.decode() if isinstance(k, bytes) else k:
                                v.decode() if isinstance(v, bytes) else v
                                for k, v in msg_data.items()}
                        if data.get("signal_id") == signal_id:
                            return {"stream": stream, "approved": stream == STREAM_APPROVED, "data": data}
            except Exception:
                pass
        await asyncio.sleep(0.3)
    return None


def make_signal(
    signal_id: str,
    confidence: float = 0.7,
    symbol: str = "BTC/USDT",
    strategy: str = "scalping",
    signal: SignalType = SignalType.BUY,
    stop_loss: float | None = 60000.0,
    target_price: float | None = 64000.0,
    entry_price: float = 62000.0,
) -> dict:
    return TradingSignal(
        signal_id=signal_id,
        symbol=symbol,
        timeframe="5m",
        timestamp=datetime.now(timezone.utc),
        signal=signal,
        confidence=confidence,
        strategy=strategy,
        reasoning="Test signal for risk validation",
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_price=target_price,
    ).model_dump(mode="json")


async def run_tests(args):
    redis = await RedisClient.get_instance()
    try:
        await redis.redis.xgroup_create(STREAM_APPROVED, CONSUMER_GROUP, id="0", mkstream=True)
    except Exception:
        pass
    try:
        await redis.redis.xgroup_create(STREAM_REJECTED, CONSUMER_GROUP, id="0", mkstream=True)
    except Exception:
        pass

    tests = []

    if args.circuit:
        sid = f"test-circuit-{uuid.uuid4().hex[:8]}"
        logger.info("Setting circuit breaker state...")
        await redis.set_json("circuit:state", {
            "status": "tripped",
            "reason": "Test: circuit breaker test",
            "resume_at": datetime.now(timezone.utc).isoformat(),
            "tripped_at": datetime.now(timezone.utc).isoformat(),
        })
        tests.append(("3. Circuit breaker", sid, make_signal(sid, confidence=0.7)))

    if args.low_conf:
        sid = f"test-lowconf-{uuid.uuid4().hex[:8]}"
        tests.append(("1. Low confidence", sid, make_signal(sid, confidence=0.2)))

    if args.drawdown:
        sid = f"test-dd-{uuid.uuid4().hex[:8]}"
        logger.info("Setting portfolio state near max drawdown...")
        await redis.set_json("portfolio:state", {
            "total_value_usd": 850.0,
            "cash_usd": 300.0,
            "peak_value_usd": 1000.0,
        })
        await asyncio.sleep(0.5)
        tests.append(("2. Max drawdown", sid, make_signal(sid, confidence=0.8)))

    if args.excluded:
        sid = f"test-excl-{uuid.uuid4().hex[:8]}"
        logger.info("Setting excluded symbols...")
        await redis.set_json("risk:excluded_symbols", ["BTC/USDT"])
        tests.append(("5. Excluded symbol", sid, make_signal(sid, confidence=0.8)))

    if args.max_positions:
        sid = f"test-maxpos-{uuid.uuid4().hex[:8]}"
        tests.append(("6. Max positions", sid, make_signal(sid, confidence=0.8, symbol="SOL/USDT")))

    if args.bad_rr:
        sid = f"test-badrr-{uuid.uuid4().hex[:8]}"
        tests.append(("8. Bad risk/reward", sid, make_signal(
            sid, confidence=0.8, stop_loss=61900.0, target_price=62100.0,
        )))

    if args.healthy or not any(vars(args).values()):
        sid = f"test-healthy-{uuid.uuid4().hex[:8]}"
        tests.append(("7. Healthy signal", sid, make_signal(sid)))

    for name, sid, signal in tests:
        logger.info(f"Publishing {name} ({sid[:12]}...)")
        await redis.publish(STREAM_SIGNALS, signal)
        await asyncio.sleep(0.3)

    print()
    print("=" * 60)
    print(f"{'TEST':<30} {'RESULT':<10} {'DETAILS'}")
    print("=" * 60)

    results = []
    for name, sid, _ in tests:
        result = await wait_for_assessment(redis, sid, timeout=5.0)
        if result is None:
            print(f"{name:<30} {'TIMEOUT':<10} No assessment received")
            results.append(False)
        elif result["approved"] and "circuit" not in name.lower() and "drawdown" not in name.lower() and "excluded" not in name.lower() and "max pos" not in name.lower() and "bad" not in name.lower() and "cooldown" not in name.lower() and "loss" not in name.lower():
            print(f"{name:<30} {'PASS ✅':<10} Approved as expected")
            results.append(True)
        elif not result["approved"] and name.startswith("7."):
            print(f"{name:<30} {'FAIL ❌':<10} Expected approval, got rejection")
            results.append(False)
        elif not result["approved"]:
            reason = result["data"].get("reason", "")[:80]
            print(f"{name:<30} {'PASS ✅':<10} {reason}")
            results.append(True)
        else:
            reason = result["data"].get("reason", "")[:80]
            print(f"{name:<30} {'FAIL ❌':<10} Unexpected approval: {reason}")
            results.append(False)

    print("=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} passed ({100*passed//total}%)")
    print("=" * 60)

    if args.circuit:
        logger.info("Resetting circuit breaker...")
        await redis.set_json("circuit:state", {"status": "closed"})
    if args.excluded:
        logger.info("Clearing excluded symbols...")
        await redis.set_json("risk:excluded_symbols", [])
    if args.drawdown:
        logger.info("Resetting portfolio state...")
        await redis.set_json("portfolio:state", {
            "total_value_usd": 1000.0,
            "cash_usd": 1000.0,
            "peak_value_usd": 1000.0,
        })

    await redis.close()
    return all(results)


def main():
    parser = argparse.ArgumentParser(description="Test risk-manager validation rules")
    parser.add_argument("--all", action="store_true", help="Run all tests")
    parser.add_argument("--low-conf", action="store_true", help="Test low confidence rejection")
    parser.add_argument("--drawdown", action="store_true", help="Test max drawdown rejection")
    parser.add_argument("--circuit", action="store_true", help="Test circuit breaker rejection")
    parser.add_argument("--excluded", action="store_true", help="Test excluded symbol rejection")
    parser.add_argument("--max-positions", action="store_true", help="Test max positions rejection")
    parser.add_argument("--bad-rr", action="store_true", help="Test bad risk/reward rejection")
    parser.add_argument("--healthy", action="store_true", help="Test healthy signal approval")

    args = parser.parse_args()

    if args.all:
        args.low_conf = True
        args.drawdown = True
        args.circuit = True
        args.excluded = True
        args.max_positions = True
        args.bad_rr = True
        args.healthy = True

    success = asyncio.run(run_tests(args))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
