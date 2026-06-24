import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.db import Database
from shared.models import TradeOrder, TradeResult, OrderStatus, SignalType
from service.portfolio import Portfolio

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("paper-trading")

INSTANCE_ID = os.getenv("PT_INSTANCE", "main")
STATE_KEY = os.getenv("PT_STATE_KEY", "paper_trading:state")
STATS_KEY = os.getenv("PT_STATS_KEY", "portfolio:stats")
STRATEGY_FILTER = os.getenv("PT_STRATEGY_FILTER", "")  # comma-separated or empty=all
MIN_CONFIDENCE = float(os.getenv("PT_MIN_CONFIDENCE", "0"))
MAX_POSITION_PCT = float(os.getenv("PT_MAX_POSITION_PCT", "0.20"))
INITIAL_CAPITAL = float(os.getenv("PT_INITIAL_CAPITAL", "1000"))

TIMEFRAME_FILTER = os.getenv("PT_TIMEFRAME_FILTER", "")  # e.g. "5m,15m"
MAX_TRADES_PER_DAY = int(os.getenv("PT_MAX_TRADES_PER_DAY", "0"))  # 0=unlimited
SENTIMENT_GATED = os.getenv("PT_SENTIMENT_GATED", "")  # "fear" or "greed" or empty=off
SENTIMENT_THRESHOLD = int(os.getenv("PT_SENTIMENT_THRESHOLD", "25"))  # fear below, greed above
TIME_FILTER = os.getenv("PT_TIME_FILTER", "")  # e.g. "5,6,8,17,22" (UTC hours) or empty=all


class PaperTradingEngine:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.db: Database | None = None
        self.portfolio: Portfolio | None = None
        self.running = False
        self.current_prices: dict[str, float] = {}
        self.trades_today: int = 0
        self.last_trade_date: str = ""

    def _parse_rsi(self, reasoning: str) -> float | None:
        m = re.search(r"RSI[^\d]*([\d.]+)", reasoning, re.IGNORECASE)
        return float(m.group(1)) if m else None

    def _parse_timeframe(self, reasoning: str) -> str | None:
        m = re.search(r"\b(\d+[mhd])\b", reasoning, re.IGNORECASE)
        return m.group(1).lower() if m else None

    def _accepts_signal(self, order: TradeOrder) -> bool:
        is_close = isinstance(order.strategy, str) and order.strategy.startswith("close_")
        if STRATEGY_FILTER and not is_close:
            allowed = [s.strip() for s in STRATEGY_FILTER.split(",")]
            if order.strategy not in allowed:
                return False

        if MIN_CONFIDENCE > 0 and (order.confidence or 0) < MIN_CONFIDENCE:
            return False

        if TIMEFRAME_FILTER:
            allowed_tf = [t.strip() for t in TIMEFRAME_FILTER.split(",")]
            tf = self._parse_timeframe(order.reasoning or "")
            if tf and tf not in allowed_tf:
                return False

        if MAX_TRADES_PER_DAY > 0:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if today != self.last_trade_date:
                self.trades_today = 0
                self.last_trade_date = today
            if self.trades_today >= MAX_TRADES_PER_DAY:
                return False

        if SENTIMENT_GATED:
            try:
                sent_data = asyncio.get_event_loop().run_until_complete(
                    self.redis.get_json("sentiment:current")
                )
                if sent_data:
                    fg_value = int(sent_data.get("fear_greed", 50))
                    if SENTIMENT_GATED == "fear" and fg_value > SENTIMENT_THRESHOLD:
                        return False
                    elif SENTIMENT_GATED == "greed" and fg_value < (100 - SENTIMENT_THRESHOLD):
                        return False
            except Exception:
                pass

        if TIME_FILTER:
            allowed_hours = [int(h.strip()) for h in TIME_FILTER.split(",")]
            current_hour = datetime.now(timezone.utc).hour
            if current_hour not in allowed_hours:
                return False

        return True

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        self.portfolio = Portfolio(INITIAL_CAPITAL, settings.BASE_CURRENCY)
        await self.load_state()
        logger.info(
            f"[{INSTANCE_ID}] Paper Trading initialized: capital=${INITIAL_CAPITAL}, "
            f"strategy_filter={STRATEGY_FILTER or 'all'}, min_conf={MIN_CONFIDENCE}, "
            f"max_pos={MAX_POSITION_PCT*100:.0f}%"
        )

    async def load_state(self):
        try:
            state = await self.redis.get_json(STATE_KEY)
            if state and state.get("positions"):
                for oid, pdata in state["positions"].items():
                    self.portfolio.positions[oid] = pdata
                    if "quantity" in pdata and "entry_price" in pdata:
                        self.portfolio.positions[oid]["quantity"] = float(pdata["quantity"])
                        self.portfolio.positions[oid]["entry_price"] = float(pdata["entry_price"])
                self.portfolio.cash = float(state.get("cash", self.portfolio.initial_capital))
                self.portfolio.total_fees = float(state.get("total_fees", 0))
                self.portfolio.total_slippage = float(state.get("total_slippage", 0))
                logger.info(f"[{INSTANCE_ID}] Restored: cash=${self.portfolio.cash:.2f}, positions={len(self.portfolio.positions)}")
        except Exception as e:
            logger.warning(f"[{INSTANCE_ID}] Could not load state: {e}")

    async def save_state(self):
        state = {
            "cash": self.portfolio.cash,
            "positions": self.portfolio.positions,
            "total_fees": self.portfolio.total_fees,
            "total_slippage": self.portfolio.total_slippage,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.redis.set_json(STATE_KEY, state)

    async def execute_order(self, data: dict):
        try:
            order = TradeOrder(**data)

            if not self._accepts_signal(order):
                return

            is_close_strategy = isinstance(order.strategy, str) and order.strategy.startswith("close_")
            has_matching_buy = any(
                isinstance(p, dict) and p.get("side") == "buy" and p.get("symbol") == order.symbol
                for p in self.portfolio.positions.values()
            )
            is_close = is_close_strategy or (str(order.side).lower().endswith("sell") and has_matching_buy)

            if is_close:
                matching_positions = [
                    oid for oid, pos in self.portfolio.positions.items()
                    if isinstance(pos, dict) and pos.get("symbol") == order.symbol
                ]
                if not matching_positions:
                    return

                for oid in matching_positions:
                    result = self.portfolio.close_position(
                        oid, order.entry_price, reason=order.reasoning,
                    )
                    if result:
                        result["signal_id"] = order.signal_id
                        await self.publish_result(result)
                        await self.store_trade(result)
            else:
                result = self.portfolio.open_position(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    side=order.side.value,
                    quantity=order.quantity,
                    entry_price=order.entry_price,
                    quantity_usd=order.quantity_usd,
                    stop_loss=order.stop_loss,
                    take_profit=order.take_profit,
                    strategy=order.strategy,
                    confidence=order.confidence,
                    reasoning=order.reasoning,
                )
                if result:
                    result["signal_id"] = order.signal_id
                    await self.publish_result(result)
                    await self.store_trade(result)
                    self.trades_today += 1

            await self.save_state()
            stats = self.portfolio.get_stats(self.current_prices)
            await self.redis.set_json(STATS_KEY, stats)
            logger.info(
                f"[{INSTANCE_ID}] value=${stats['total_value']:.2f} "
                f"PnL=${stats['total_pnl']:.2f} ({stats['total_pnl_pct']:.1f}%) "
                f"trades={stats['total_trades']} wr={stats['win_rate']:.0f}%"
            )

        except Exception as e:
            logger.error(f"[{INSTANCE_ID}] Error executing order: {e}")

    async def publish_result(self, result: dict):
        trade_result = TradeResult(
            order_id=result["order_id"],
            symbol=result["symbol"],
            side=SignalType(result["side"]),
            entry_price=result["entry_price"],
            exit_price=result.get("exit_price", result["entry_price"]),
            quantity=result["quantity"],
            quantity_usd=result["quantity_usd"],
            fee_usd=result["fee_usd"],
            slippage_usd=result.get("slippage_usd", 0),
            pnl_usd=result["pnl_usd"],
            status=OrderStatus(result["status"]),
            strategy=result.get("strategy", ""),
            confidence=result.get("confidence", 0),
            reasoning=result.get("reasoning", ""),
            timestamp=datetime.now(timezone.utc),
            stop_loss=result.get("stop_loss"),
            take_profit=result.get("take_profit"),
        )
        await self.redis.publish(settings.STREAM_TRADE_RESULTS, trade_result.model_dump(mode="json"))
        await self.redis.publish("trade:log", result)

    async def store_trade(self, result: dict):
        try:
            await self.db.execute(
                """INSERT INTO trades (time, order_id, symbol, side, entry_price, exit_price,
                   quantity, quantity_usd, fee_usd, pnl_usd, status, strategy, confidence, reasoning,
                   stop_loss, take_profit)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)""",
                datetime.now(timezone.utc), result["order_id"], result["symbol"],
                result["side"], result["entry_price"], result.get("exit_price", result["entry_price"]),
                result["quantity"], result["quantity_usd"], result["fee_usd"],
                result["pnl_usd"], result["status"], result.get("strategy", ""),
                result.get("confidence", 0), result.get("reasoning", ""),
                result.get("stop_loss"), result.get("take_profit"),
            )
        except Exception as e:
            logger.error(f"[{INSTANCE_ID}] Error storing trade: {e}")

    async def update_prices(self, data: dict):
        try:
            symbol = data.get("symbol", "")
            price = float(data.get("close", 0))
            if symbol and price > 0:
                self.current_prices[symbol] = price
        except Exception:
            pass

    async def periodic_stats(self):
        while self.running:
            try:
                await asyncio.sleep(30)
                stats = self.portfolio.get_stats(self.current_prices)
                await self.redis.set_json(STATS_KEY, stats)

                total_value = stats["total_value"]
                cash = stats["cash"]
                positions_data = {}
                for oid, pos in self.portfolio.positions.items():
                    symbol = pos["symbol"]
                    current_price = self.current_prices.get(symbol, pos["entry_price"])
                    unrealized = pos["quantity"] * (current_price - pos["entry_price"]) if pos["side"] == "buy" else pos["quantity"] * (pos["entry_price"] - current_price)
                    positions_data[oid] = {
                        "symbol": symbol,
                        "side": pos["side"],
                        "quantity": pos["quantity"],
                        "entry_price": pos["entry_price"],
                        "current_value": pos["quantity"] * current_price,
                        "unrealized_pnl": unrealized,
                    }

                await self.db.execute(
                    """INSERT INTO portfolio_snapshots (time, total_value_usd, cash_usd, positions)
                       VALUES ($1, $2, $3, $4)""",
                    datetime.now(timezone.utc), total_value, cash,
                    json.dumps(positions_data),
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{INSTANCE_ID}] Stats error: {e}")

    async def run(self):
        self.running = True
        await self.initialize()
        order_group = f"paper-trading-orders-{INSTANCE_ID}"
        order_consumer = f"paper-trading-{INSTANCE_ID}"
        market_group = f"paper-trading-market-{INSTANCE_ID}"
        market_consumer = f"paper-trading-market-{INSTANCE_ID}"

        stats_task = asyncio.create_task(self.periodic_stats())

        logger.info(f"[{INSTANCE_ID}] Paper Trading Engine running")
        while self.running:
            try:
                orders = await self.redis.read_stream(
                    settings.STREAM_TRADE_ORDERS, order_group, order_consumer, count=5, block=2000,
                )
                for msg_id, data in orders:
                    await self.execute_order(data)

                market = await self.redis.read_stream(
                    settings.STREAM_MARKET_DATA, market_group, market_consumer, count=20, block=1000,
                )
                for msg_id, data in market:
                    await self.update_prices(data)

            except asyncio.CancelledError:
                self.running = False
                break
            except Exception as e:
                logger.error(f"[{INSTANCE_ID}] Paper trading error: {e}")
                await asyncio.sleep(3)

        stats_task.cancel()


async def main():
    engine = PaperTradingEngine()
    await engine.run()


if __name__ == "__main__":
    asyncio.run(main())
