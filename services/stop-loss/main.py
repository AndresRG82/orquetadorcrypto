import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.models import SignalType, TradeOrder

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("stop-loss-monitor")

TRAILING_CONFIG = {
    "scalping": {"activation_pct": 2.5, "trail_atr_mult": 2.0, "breakeven_pct": 1.5},
    "swing": {"activation_pct": 3.0, "trail_atr_mult": 1.5, "breakeven_pct": 1.5},
    "arbitrage": {"activation_pct": 2.0, "trail_atr_mult": 1.0, "breakeven_pct": 1.0},
    "qwen_direct": {"activation_pct": 2.0, "trail_atr_mult": 1.5, "breakeven_pct": 1.0},
}
DEFAULT_TRAILING = {"activation_pct": 2.0, "trail_atr_mult": 1.5, "breakeven_pct": 1.0}


PT_STATE_KEYS = [
    "paper_trading:state",
]



class StopLossMonitor:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.running = False
        self.positions: dict[str, dict] = {}
        self.current_prices: dict[str, float] = {}
        self.indicators_cache: dict[str, dict] = {}
        self.check_interval = 5

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        await self.load_positions()
        logger.info(f"Stop-Loss Monitor initialized with {len(self.positions)} positions from {len(PT_STATE_KEYS)} instances (trailing enabled)")

    async def load_positions(self):
        for key in PT_STATE_KEYS:
            try:
                state = await self.redis.get_json(key)
                if state and "positions" in state:
                    count = 0
                    for oid, pdata in state["positions"].items():
                        if isinstance(pdata, dict):
                            pdata["_state_key"] = key
                            self.positions[oid] = pdata
                            symbol = pdata.get("symbol", "")
                            if pdata.get("entry_price"):
                                self.current_prices[symbol] = float(pdata["entry_price"])
                            count += 1
                    if count > 0:
                        logger.info(f"Loaded {count} positions from {key}")
            except Exception as e:
                logger.debug(f"Could not load positions from {key}: {e}")

    def compute_trailing_stop(self, pos: dict, current_price: float) -> Optional[float]:
        symbol = pos.get("symbol", "")
        side = pos.get("side", "buy")
        entry = float(pos.get("entry_price", 0))
        strategy = pos.get("strategy", "").replace("close_", "")
        original_stop = float(pos.get("stop_loss", 0)) if pos.get("stop_loss") else None

        config = TRAILING_CONFIG.get(strategy, DEFAULT_TRAILING)
        activation_pct = config["activation_pct"]
        trail_atr_mult = config["trail_atr_mult"]
        breakeven_pct = config["breakeven_pct"]

        if entry <= 0:
            return original_stop

        pnl_pct = (current_price - entry) / entry * 100 if side == "buy" else (entry - current_price) / entry * 100
        best_pnl_pct = float(pos.get("best_pnl_pct", 0))
        if pnl_pct > best_pnl_pct:
            pos["best_pnl_pct"] = pnl_pct
            best_pnl_pct = pnl_pct

        atr = None
        ind = self.indicators_cache.get(f"{symbol}:latest")
        if ind and ind.get("atr_14"):
            atr = float(ind["atr_14"])

        new_stop = original_stop

        if best_pnl_pct >= breakeven_pct and new_stop is not None:
            if side == "buy" and new_stop < entry:
                new_stop = entry
            elif side == "sell" and (new_stop is None or new_stop > entry):
                new_stop = entry

        if best_pnl_pct >= activation_pct:
            if atr and atr > 0:
                trail_distance = atr * trail_atr_mult
                if side == "buy":
                    atr_stop = current_price - trail_distance
                    if new_stop is None or atr_stop > new_stop:
                        new_stop = atr_stop
                else:
                    atr_stop = current_price + trail_distance
                    if new_stop is None or atr_stop < new_stop:
                        new_stop = atr_stop
            else:
                if side == "buy":
                    fixed_pct_stop = entry * (1 + (best_pnl_pct - activation_pct * 0.5) / 100)
                    if new_stop is None or fixed_pct_stop > new_stop:
                        new_stop = fixed_pct_stop
                else:
                    fixed_pct_stop = entry * (1 - (best_pnl_pct - activation_pct * 0.5) / 100)
                    if new_stop is None or fixed_pct_stop < new_stop:
                        new_stop = fixed_pct_stop

        if side == "buy" and new_stop is not None and current_price <= new_stop:
            return new_stop
        if side == "sell" and new_stop is not None and current_price >= new_stop:
            return new_stop

        if new_stop != original_stop:
            pos["stop_loss"] = new_stop
            if new_stop and original_stop:
                direction = "tightened" if (side == "buy" and new_stop > original_stop) or (side == "sell" and new_stop < original_stop) else "widened"
                logger.debug(f"Trailing stop {direction} for {symbol}: {original_stop:.6f} -> {new_stop:.6f}")

        return None

    async def check_stops(self):
        orders_to_send = []

        for oid, pos in list(self.positions.items()):
            symbol = pos.get("symbol", "")
            current_price = self.current_prices.get(symbol)
            if current_price is None:
                continue

            side = pos.get("side", "buy")
            should_close = False
            reason = ""
            stop_price = None

            stop_loss = pos.get("stop_loss")
            take_profit = pos.get("take_profit")

            if stop_loss:
                sl = float(stop_loss)
                if side == "buy" and current_price <= sl:
                    should_close = True
                    reason = f"Stop loss hit: {current_price:.6f} <= {sl:.6f}"
                    stop_price = sl
                elif side == "sell" and current_price >= sl:
                    should_close = True
                    reason = f"Stop loss hit: {current_price:.6f} >= {sl:.6f}"
                    stop_price = sl

            if take_profit and not should_close:
                tp = float(take_profit)
                if side == "buy" and current_price >= tp:
                    should_close = True
                    reason = f"Take profit hit: {current_price:.6f} >= {tp:.6f}"
                elif side == "sell" and current_price <= tp:
                    should_close = True
                    reason = f"Take profit hit: {current_price:.6f} <= {tp:.6f}"

            if not should_close:
                trailing_hit = self.compute_trailing_stop(pos, current_price)
                if trailing_hit is not None:
                    should_close = True
                    reason = f"Trailing stop hit at {trailing_hit:.6f} (price: {current_price:.6f})"

            if should_close:
                quantity = float(pos.get("quantity", 0))
                if quantity <= 0:
                    continue

                close_side = SignalType.SELL if side == "buy" else SignalType.BUY
                order = TradeOrder(
                    order_id=str(uuid.uuid4()),
                    signal_id=oid,
                    symbol=symbol,
                    side=close_side,
                    entry_price=current_price,
                    quantity_usd=quantity * current_price,
                    quantity=quantity,
                    stop_loss=None,
                    take_profit=None,
                    strategy=f"close_{pos.get('strategy', 'unknown')}",
                    confidence=1.0,
                    reasoning=reason,
                    timestamp=datetime.now(timezone.utc),
                )
                orders_to_send.append(order)
                del self.positions[oid]
                logger.info(f"STOP/TARGET/TRAIL: {reason} for {symbol}")

        for order in orders_to_send:
            await self.redis.publish(settings.STREAM_TRADE_ORDERS, order.model_dump(mode="json"))

    async def process_market_data(self, data: dict):
        try:
            symbol = data.get("symbol", "")
            price = float(data.get("close", 0))
            if symbol and price > 0:
                self.current_prices[symbol] = price
        except Exception:
            pass

    async def process_indicators(self, data: dict):
        try:
            symbol = data.get("symbol", "")
            timeframe = data.get("timeframe", "")
            if timeframe in ("1m", "5m"):
                self.indicators_cache[f"{symbol}:latest"] = data
        except Exception:
            pass

    async def process_trade_results(self, data: dict):
        try:
            status = str(data.get("status", ""))
            order_id = data.get("order_id", "")

            if status == "open":
                symbol = data.get("symbol", "")
                sl = data.get("stop_loss")
                tp = data.get("take_profit")
                if sl or tp:
                    self.positions[order_id] = {
                        "order_id": order_id,
                        "symbol": symbol,
                        "side": data.get("side", "buy"),
                        "quantity": data.get("quantity", 0),
                        "entry_price": float(data.get("entry_price", 0)),
                        "stop_loss": sl,
                        "take_profit": tp,
                        "strategy": data.get("strategy", ""),
                        "best_pnl_pct": 0,
                    }
            elif status == "closed":
                signal_id = data.get("signal_id", "")
                if signal_id in self.positions:
                    del self.positions[signal_id]
                elif order_id in self.positions:
                    del self.positions[order_id]
        except Exception as e:
            logger.error(f"Error processing trade result: {e}")

    async def run(self):
        self.running = True
        await self.initialize()
        market_group = "stop-loss-market"
        market_consumer = "stop-loss-market-1"
        results_group = "stop-loss-results"
        results_consumer = "stop-loss-results-1"
        indicators_group = "stop-loss-indicators"
        indicators_consumer = "stop-loss-ind-1"

        logger.info("Stop-Loss Monitor running (with trailing stops)")
        while self.running:
            try:
                await self.redis.heartbeat("stop-loss")
                market = await self.redis.read_stream(settings.STREAM_MARKET_DATA, market_group, market_consumer, count=50, block=2000)
                for msg_id, data in market:
                    await self.process_market_data(data)

                indicators = await self.redis.read_stream(settings.STREAM_INDICATORS, indicators_group, indicators_consumer, count=20, block=500)
                for msg_id, data in indicators:
                    await self.process_indicators(data)

                results = await self.redis.read_stream(settings.STREAM_TRADE_RESULTS, results_group, results_consumer, count=20, block=1000)
                for msg_id, data in results:
                    await self.process_trade_results(data)

                if self.positions:
                    await self.check_stops()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Stop-Loss Monitor error: {e}")
                await asyncio.sleep(5)


async def main():
    monitor = StopLossMonitor()
    await monitor.run()


if __name__ == "__main__":
    asyncio.run(main())
