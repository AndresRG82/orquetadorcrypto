import asyncio
import json
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.db import Database
from shared.models import (
    TradingSignal, RiskAssessment, TradeOrder, TradeResult,
    PortfolioSnapshot, SignalType, OrderStatus, Position,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("orchestrator")


class Orchestrator:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.db: Database | None = None
        self.running = False
        self.initial_capital = settings.INITIAL_CAPITAL
        self.base_currency = settings.BASE_CURRENCY
        self.cash: float = self.initial_capital
        self.positions: dict[str, Position] = {}
        self.closed_pnl: float = 0.0
        self.signal_cache: dict[str, TradingSignal] = {}

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        await self.load_portfolio()
        await self.redis.set_json("orchestrator:status", {"status": "running", "started_at": datetime.now(timezone.utc).isoformat()})
        logger.info(f"Orchestrator initialized: capital=${self.initial_capital}, base={self.base_currency}")

    async def load_portfolio(self):
        try:
            state = await self.redis.get_json("portfolio:orchestrator")
            if state:
                self.cash = float(state.get("cash", self.initial_capital))
                self.closed_pnl = float(state.get("closed_pnl", 0))
                positions_data = state.get("positions", {})
                for oid, pdata in positions_data.items():
                    self.positions[oid] = Position(**pdata)
                logger.info(f"Loaded own portfolio: cash=${self.cash:.2f}, positions={len(self.positions)}")

            pt_state = await self.redis.get_json("paper_trading:state")
            if pt_state:
                pt_cash = float(pt_state.get("cash", self.initial_capital))
                pt_positions = pt_state.get("positions", {})
                total_position_value = sum(
                    float(p.get("quantity", 0)) * float(p.get("entry_price", 0))
                    for p in pt_positions.values() if isinstance(p, dict)
                )
                real_value = pt_cash + total_position_value

                if not state or abs(self.cash - pt_cash) > pt_cash * 0.5 or len(self.positions) != len(pt_positions):
                    logger.warning(
                        f"Syncing from paper-trading: orchestrator had cash=${self.cash:.2f}/{len(self.positions)} pos, "
                        f"paper-trading has cash=${pt_cash:.2f}/{len(pt_positions)} pos"
                    )
                    self.cash = pt_cash
                    self.positions = {}
                    for oid, pdata in pt_positions.items():
                        if isinstance(pdata, dict):
                            self.positions[oid] = Position(
                                symbol=pdata.get("symbol", ""),
                                side=SignalType.BUY if pdata.get("side", "buy") == "buy" else SignalType.SELL,
                                quantity=float(pdata.get("quantity", 0)),
                                entry_price=float(pdata.get("entry_price", 0)),
                                quantity_usd=float(pdata.get("quantity_usd", 0)),
                                stop_loss=float(pdata.get("stop_loss", 0)) if pdata.get("stop_loss") else None,
                                take_profit=float(pdata.get("take_profit", 0)) if pdata.get("take_profit") else None,
                                strategy=pdata.get("strategy", ""),
                                order_id=oid,
                                opened_at=datetime.now(timezone.utc),
                            )
                    await self.save_portfolio()
                    logger.info(f"Synced portfolio: cash=${self.cash:.2f}, positions={len(self.positions)}")
        except Exception as e:
            logger.warning(f"Could not load portfolio: {e}")

    async def save_portfolio(self):
        state = {
            "cash": self.cash,
            "closed_pnl": self.closed_pnl,
            "positions": {oid: p.model_dump(mode="json") for oid, p in self.positions.items()},
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.redis.set_json("portfolio:orchestrator", state)

    async def process_approved_signal(self, data: dict):
        try:
            assessment = RiskAssessment(**data)

            if not assessment.approved:
                return

            signal_id = assessment.signal_id
            position_size = assessment.position_size_usd or 0

            if position_size <= 0:
                logger.warning(f"Approved signal {signal_id} has zero position size")
                return

            signal_data = self.signal_cache.get(signal_id)
            if not signal_data:
                cached_json = await self.redis.get_json(f"signal:{signal_id}")
                if cached_json:
                    signal_data = TradingSignal(**cached_json)

            if not signal_data:
                logger.warning(f"Signal data not found for {signal_id}, skipping")
                return

            signal = signal_data
            quantity = position_size / signal.entry_price if signal.entry_price > 0 else 0

            order = TradeOrder(
                order_id=str(uuid.uuid4()),
                signal_id=signal_id,
                symbol=signal.symbol,
                side=signal.signal,
                entry_price=signal.entry_price,
                quantity_usd=position_size,
                quantity=round(quantity, 8),
                stop_loss=assessment.adjusted_stop_loss or signal.stop_loss,
                take_profit=signal.target_price,
                strategy=signal.strategy,
                confidence=signal.confidence,
                reasoning=signal.reasoning,
                timestamp=datetime.now(timezone.utc),
            )

            await self.redis.publish(settings.STREAM_TRADE_ORDERS, order.model_dump(mode="json"))
            await self.redis.set_json(f"signal:{signal_id}", signal.model_dump(mode="json"), ex=3600)
            await self.redis.set("last_signal", signal.model_dump_json(), ex=3600)

            logger.info(
                f"ORDER: {order.side.value} {order.symbol} qty={order.quantity:.6f} "
                f"(${order.quantity_usd:.2f}) strategy={order.strategy}"
            )

        except Exception as e:
            logger.error(f"Error processing approved signal: {e}")

    async def check_stop_losses_and_targets(self, market_data: dict):
        try:
            symbol = market_data.get("symbol", "")
            current_price = float(market_data.get("close", 0))

            if current_price <= 0:
                return

            for oid, position in list(self.positions.items()):
                if position.symbol != symbol:
                    continue

                should_close = False
                reason = ""

                if position.stop_loss:
                    if position.side == SignalType.BUY and current_price <= position.stop_loss:
                        should_close = True
                        reason = f"Stop loss hit: {current_price} <= {position.stop_loss}"
                    elif position.side == SignalType.SELL and current_price >= position.stop_loss:
                        should_close = True
                        reason = f"Stop loss hit: {current_price} >= {position.stop_loss}"

                if position.take_profit and not should_close:
                    if position.side == SignalType.BUY and current_price >= position.take_profit:
                        should_close = True
                        reason = f"Take profit hit: {current_price} >= {position.take_profit}"
                    elif position.side == SignalType.SELL and current_price <= position.take_profit:
                        should_close = True
                        reason = f"Take profit hit: {current_price} <= {position.take_profit}"

                if should_close:
                    close_order = TradeOrder(
                        order_id=str(uuid.uuid4()),
                        signal_id=position.order_id,
                        symbol=position.symbol,
                        side=SignalType.SELL if position.side == SignalType.BUY else SignalType.BUY,
                        entry_price=current_price,
                        quantity_usd=position.quantity * current_price,
                        quantity=position.quantity,
                        strategy=f"close_{position.strategy}",
                        confidence=1.0,
                        reasoning=reason,
                        timestamp=datetime.now(timezone.utc),
                    )
                    await self.redis.publish(settings.STREAM_TRADE_ORDERS, close_order.model_dump(mode="json"))
                    logger.info(f"CLOSE ORDER: {reason} for {position.symbol}")

        except Exception as e:
            logger.error(f"Error checking stop losses: {e}")

    async def update_from_trade_results(self, data: dict):
        try:
            status = data.get("status", "")
            order_id = data.get("order_id", "")
            symbol = data.get("symbol", "")
            side = data.get("side", "")
            pnl = float(data.get("pnl_usd", 0))
            quantity_usd = float(data.get("quantity_usd", 0))

            if status in ("opened", "open"):
                self.cash -= quantity_usd
                if side == "buy":
                    pos = Position(
                        symbol=symbol,
                        side=SignalType.BUY,
                        quantity=float(data.get("quantity", 0)),
                        entry_price=float(data.get("entry_price", 0)),
                        quantity_usd=quantity_usd,
                        stop_loss=float(data.get("stop_loss", 0)) if data.get("stop_loss") else None,
                        take_profit=float(data.get("take_profit", 0)) if data.get("take_profit") else None,
                        strategy=data.get("strategy", ""),
                        order_id=order_id,
                        opened_at=datetime.now(timezone.utc),
                    )
                    self.positions[order_id] = pos

            elif status == "closed":
                self.cash += quantity_usd + pnl
                self.closed_pnl += pnl
                if order_id in self.positions:
                    del self.positions[order_id]

            await self.save_portfolio()

            total_value = self.cash + sum(p.quantity_usd for p in self.positions.values())
            snapshot = PortfolioSnapshot(
                timestamp=datetime.now(timezone.utc),
                total_value_usd=total_value,
                cash_usd=self.cash,
                positions=list(self.positions.values()),
                unrealized_pnl_usd=self.closed_pnl,
            )
            try:
                await self.db.execute(
                    """INSERT INTO portfolio_snapshots (time, total_value_usd, cash_usd, positions)
                       VALUES ($1, $2, $3, $4)""",
                    snapshot.timestamp, snapshot.total_value_usd, snapshot.cash_usd,
                    json.dumps([p.model_dump(mode="json") for p in snapshot.positions]),
                )
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Error updating from trade result: {e}")

    async def sync_from_paper_trading(self):
        try:
            pt_state = await self.redis.get_json("paper_trading:state")
            if pt_state:
                self.cash = float(pt_state.get("cash", self.cash))
                self.positions = {}
                for oid, pdata in pt_state.get("positions", {}).items():
                    if isinstance(pdata, dict):
                        self.positions[oid] = Position(
                            symbol=pdata.get("symbol", ""),
                            side=SignalType.BUY if pdata.get("side", "buy") == "buy" else SignalType.SELL,
                            quantity=float(pdata.get("quantity", 0)),
                            entry_price=float(pdata.get("entry_price", 0)),
                            quantity_usd=float(pdata.get("quantity_usd", 0)),
                            stop_loss=float(pdata.get("stop_loss", 0)) if pdata.get("stop_loss") else None,
                            take_profit=float(pdata.get("take_profit", 0)) if pdata.get("take_profit") else None,
                            strategy=pdata.get("strategy", ""),
                            order_id=oid,
                            opened_at=datetime.now(timezone.utc),
                        )
                logger.info(f"Synced from paper trading: cash=${self.cash:.2f}, positions={len(self.positions)}")
        except Exception as e:
            logger.warning(f"Sync error: {e}")

    async def periodic_snapshot(self):
        while self.running:
            try:
                await asyncio.sleep(60)
                total_value = self.cash + sum(p.quantity_usd for p in self.positions.values())
                snapshot = PortfolioSnapshot(
                    timestamp=datetime.now(timezone.utc),
                    total_value_usd=total_value,
                    cash_usd=self.cash,
                    positions=list(self.positions.values()),
                    unrealized_pnl_usd=self.closed_pnl,
                )
                await self.redis.set_json("portfolio:latest", snapshot.model_dump(mode="json"))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Snapshot error: {e}")

    async def cache_signals(self, data: dict):
        try:
            signal = TradingSignal(**data)
            self.signal_cache[signal.signal_id] = signal
            if len(self.signal_cache) > 500:
                keys = list(self.signal_cache.keys())
                for k in keys[:250]:
                    del self.signal_cache[k]
        except Exception:
            pass

    async def run(self):
        self.running = True
        await self.initialize()
        approved_group = "orchestrator-approved"
        approved_consumer = "orchestrator-1"
        results_group = "orchestrator-results"
        results_consumer = "orchestrator-results-1"
        market_group = "orchestrator-market"
        market_consumer = "orchestrator-market-1"
        signals_group = "orchestrator-signals"
        signals_consumer = "orchestrator-signals-1"

        snapshot_task = asyncio.create_task(self.periodic_snapshot())
        sync_counter = 0

        logger.info("Orchestrator running")
        while self.running:
            try:
                signals = await self.redis.read_stream(
                    settings.STREAM_SIGNALS, signals_group, signals_consumer, count=20, block=500,
                )
                for msg_id, data in signals:
                    await self.cache_signals(data)

                approved = await self.redis.read_stream(
                    settings.STREAM_RISK_APPROVED, approved_group, approved_consumer, count=5, block=2000,
                )
                for msg_id, data in approved:
                    await self.process_approved_signal(data)

                results = await self.redis.read_stream(
                    settings.STREAM_TRADE_RESULTS, results_group, results_consumer, count=10, block=1000,
                )
                for msg_id, data in results:
                    await self.update_from_trade_results(data)

                market = await self.redis.read_stream(
                    settings.STREAM_MARKET_DATA, market_group, market_consumer, count=10, block=1000,
                )
                for msg_id, data in market:
                    await self.check_stop_losses_and_targets(data)

                sync_counter += 1
                if sync_counter % 30 == 0:
                    await self.sync_from_paper_trading()

            except asyncio.CancelledError:
                self.running = False
                break
            except Exception as e:
                logger.error(f"Orchestrator error: {e}")
                await asyncio.sleep(5)

        snapshot_task.cancel()


async def main():
    orch = Orchestrator()
    await orch.run()


if __name__ == "__main__":
    asyncio.run(main())