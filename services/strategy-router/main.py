import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.db import Database
from shared.models import TradingSignal, SignalType

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("strategy-router")

MIN_TRADES_FOR_STATS = 5
DEACTIVATE_WIN_RATE = 0.15
BOOST_WIN_RATE = 0.55
KELLY_FRACTION = 0.5


class StrategyRouter:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.db: Database | None = None
        self.running = False
        self.strategy_stats: dict[str, dict] = {}
        self.kelly_sizes: dict[str, float] = {}
        self.combined_signals: dict[str, list[TradingSignal]] = {}

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        await self.load_stats()
        logger.info(f"Strategy Router initialized with {len(self.strategy_stats)} strategies")

    async def load_stats(self):
        try:
            rows = await self.db.fetch(
                "SELECT strategy, COUNT(*) as total, SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins, "
                "SUM(CASE WHEN pnl_usd > 0 THEN pnl_usd ELSE 0 END) as total_wins, "
                "SUM(CASE WHEN pnl_usd <= 0 THEN ABS(pnl_usd) ELSE 0 END) as total_losses "
                "FROM trades WHERE status = 'closed' AND time > NOW() - INTERVAL '7 days' GROUP BY strategy"
            )
            for r in rows:
                strategy = r["strategy"] or "unknown"
                total = int(r["total"]) if r["total"] else 0
                wins = int(r["wins"]) if r["wins"] else 0
                total_wins = float(r["total_wins"]) if r["total_wins"] else 0
                total_losses = float(r["total_losses"]) if r["total_losses"] else 0
                win_rate = (wins / total * 100) if total > 0 else 50.0
                avg_win = total_wins / wins if wins > 0 else 0
                avg_loss = total_losses / (total - wins) if (total - wins) > 0 else 1
                payoff_ratio = avg_win / avg_loss if avg_loss > 0 else 1.0

                kelly = 0
                if total >= MIN_TRADES_FOR_STATS and wins > 0 and (total - wins) > 0:
                    p = wins / total
                    q = 1 - p
                    kelly = (p * payoff_ratio - q) / payoff_ratio
                    kelly = max(0, min(kelly, 0.25)) * KELLY_FRACTION

                evo_config = await self.redis.get_json(f"strategy:config:{strategy}") or {}
                evo_active = evo_config.get("active", True)
                evo_weight = float(evo_config.get("confidence_weight", 1.0))
                db_active = win_rate >= DEACTIVATE_WIN_RATE if total >= MIN_TRADES_FOR_STATS else True

                self.strategy_stats[strategy] = {
                    "total": total, "wins": wins, "win_rate": win_rate,
                    "avg_win": avg_win, "avg_loss": avg_loss,
                    "payoff_ratio": payoff_ratio, "kelly": kelly,
                    "active": db_active and evo_active if total >= MIN_TRADES_FOR_STATS else evo_active,
                    "confidence_weight": evo_weight,
                }
                self.kelly_sizes[strategy] = kelly if kelly > 0 else 0.10
                logger.info(f"Strategy {strategy}: win_rate={win_rate:.1f}% kelly={kelly:.3f} active={self.strategy_stats[strategy]['active']} weight={evo_weight}")
        except Exception as e:
            logger.warning(f"Could not load stats: {e}")

    def get_position_multiplier(self, strategy: str) -> float:
        stats = self.strategy_stats.get(strategy)
        if not stats:
            return 0.15
        if not stats["active"]:
            return 0.0
        if stats["win_rate"] >= BOOST_WIN_RATE and stats["total"] >= MIN_TRADES_FOR_STATS:
            return min(0.25, stats["kelly"] * 2)
        return stats["kelly"] if stats["kelly"] > 0 else 0.10

    def combine_signals(self, symbol: str) -> Optional[TradingSignal]:
        signals = self.combined_signals.get(symbol, [])
        if len(signals) < 2:
            return None

        buy_signals = [s for s in signals if s.signal == SignalType.BUY]
        sell_signals = [s for s in signals if s.signal == SignalType.SELL]

        if len(buy_signals) > len(sell_signals) and len(buy_signals) >= 2:
            combined_confidence = sum(s.confidence for s in buy_signals) / len(buy_signals) + 0.1 * (len(buy_signals) - 1)
            combined_confidence = min(0.95, combined_confidence)
            best = max(buy_signals, key=lambda s: s.confidence)
            reasoning = f"[COMBINED {len(buy_signals)}x BUY] " + "; ".join(s.reasoning[:60] for s in buy_signals[:3])
            return TradingSignal(
                signal_id=str(uuid.uuid4()), symbol=symbol, timeframe=best.timeframe,
                timestamp=datetime.now(timezone.utc), signal=SignalType.BUY,
                confidence=combined_confidence, strategy="combined",
                reasoning=reasoning, entry_price=best.entry_price,
                target_price=best.target_price, stop_loss=best.stop_loss,
            )
        elif len(sell_signals) > len(buy_signals) and len(sell_signals) >= 2:
            combined_confidence = sum(s.confidence for s in sell_signals) / len(sell_signals) + 0.1 * (len(sell_signals) - 1)
            combined_confidence = min(0.95, combined_confidence)
            best = max(sell_signals, key=lambda s: s.confidence)
            reasoning = f"[COMBINED {len(sell_signals)}x SELL] " + "; ".join(s.reasoning[:60] for s in sell_signals[:3])
            return TradingSignal(
                signal_id=str(uuid.uuid4()), symbol=symbol, timeframe=best.timeframe,
                timestamp=datetime.now(timezone.utc), signal=SignalType.SELL,
                confidence=combined_confidence, strategy="combined",
                reasoning=reasoning, entry_price=best.entry_price,
                target_price=best.target_price, stop_loss=best.stop_loss,
            )
        return None

    async def process_signal(self, data: dict):
        try:
            signal = TradingSignal(**data)
            strategy = signal.strategy
            multiplier = self.get_position_multiplier(strategy)

            if multiplier == 0.0:
                logger.info(f"ROUTER: Strategy {strategy} deactivated (low win rate), dropping signal for {signal.symbol}")
                return

            self.combined_signals.setdefault(signal.symbol, []).append(signal)

            enriched = signal.model_dump(mode="json")
            enriched["position_multiplier"] = multiplier
            enriched["strategy_active"] = self.strategy_stats.get(strategy, {}).get("active", True)

            combined = self.combine_signals(signal.symbol)
            if combined:
                await self.redis.publish(settings.STREAM_SIGNALS, combined.model_dump(mode="json"))
                logger.info(f"ROUTER: Combined signal for {signal.symbol}: {combined.signal.value} conf={combined.confidence:.2f}")

            await self.redis.publish(settings.STREAM_SIGNALS, enriched)
            self.combined_signals[signal.symbol] = self.combined_signals[signal.symbol][-5:]

        except Exception as e:
            logger.error(f"Error processing signal: {e}")

    async def run(self):
        self.running = True
        await self.initialize()
        group = "strategy-router"
        consumer = "router-1"

        logger.info("Strategy Router running (Kelly + performance routing)")
        while self.running:
            try:
                messages = await self.redis.read_stream(settings.STREAM_SIGNALS, group, consumer, count=20, block=2000)
                for msg_id, data in messages:
                    await self.process_signal(data)

                await asyncio.sleep(60)
                await self.load_stats()
                expired = [k for k, v in self.combined_signals.items() if not v]
                for k in expired:
                    del self.combined_signals[k]

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Strategy Router error: {e}")
                await asyncio.sleep(5)


async def main():
    router = StrategyRouter()
    await router.run()


if __name__ == "__main__":
    asyncio.run(main())
