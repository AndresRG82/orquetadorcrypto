import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import ccxt.async_support as ccxt

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
VENUE = os.getenv("PT_VENUE", "paper")  # "paper", "bybit_testnet", "binance_paper", etc.
STRATEGY_FILTER = os.getenv("PT_STRATEGY_FILTER", "")  # comma-separated or empty=all
MIN_CONFIDENCE = float(os.getenv("PT_MIN_CONFIDENCE", "0"))
MAX_POSITION_PCT = float(os.getenv("PT_MAX_POSITION_PCT", "0.20"))
INITIAL_CAPITAL = float(os.getenv("PT_INITIAL_CAPITAL", "1000"))

TIMEFRAME_FILTER = os.getenv("PT_TIMEFRAME_FILTER", "")  # e.g. "5m,15m"
MAX_TRADES_PER_DAY = int(os.getenv("PT_MAX_TRADES_PER_DAY", "0"))  # 0=unlimited
SENTIMENT_GATED = os.getenv("PT_SENTIMENT_GATED", "")  # "fear" or "greed" or empty=off
SENTIMENT_THRESHOLD = int(os.getenv("PT_SENTIMENT_THRESHOLD", "25"))  # fear below, greed above
TIME_FILTER = os.getenv("PT_TIME_FILTER", "")  # e.g. "5,6,8,17,22" (UTC hours) or empty=all
SWAP_ENABLED = os.getenv("PT_SWAP_ENABLED", "").lower() == "true"
LEVERAGE = float(os.getenv("PT_LEVERAGE", "1"))

MAX_POSITIONS_PAPER = int(os.getenv("MAX_POSITIONS_PAPER", "10"))
MAX_POSITIONS_OKX_SPOT = int(os.getenv("MAX_POSITIONS_OKX_SPOT", "6"))
MAX_POSITIONS_OKX_SWAP = int(os.getenv("MAX_POSITIONS_OKX_SWAP", "4"))
MAX_POSITIONS_BY_VENUE: dict[str, int] = {
    "paper": MAX_POSITIONS_PAPER,
    "okx_testnet": MAX_POSITIONS_OKX_SPOT,
    "okx_swap_testnet": MAX_POSITIONS_OKX_SWAP,
}


class PaperTradingEngine:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.db: Database | None = None
        self.portfolio: Portfolio | None = None
        self.running = False
        self.current_prices: dict[str, float] = {}
        self.trades_today: int = 0
        self.last_trade_date: str = ""
        self.exchange: ccxt.Exchange | None = None
        self.use_testnet = False

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

    async def _init_exchange(self):
        if not settings.PAPER_API_KEY:
            logger.info(f"[{INSTANCE_ID}] No paper API key, using in-memory simulation")
            return
        try:
            exchange_opts = {
                "apiKey": settings.PAPER_API_KEY,
                "secret": settings.PAPER_API_SECRET,
                "enableRateLimit": True,
                "options": {"defaultType": "swap" if SWAP_ENABLED else "spot"},
            }
            pw = os.getenv(f"{settings.PAPER_EXCHANGE.upper()}_API_PASSWORD", "")
            if pw:
                exchange_opts["password"] = pw
            if settings.PAPER_EXCHANGE == "bybit":
                exchange_opts["options"]["enableUnifiedAccount"] = True
            exchange_cls = getattr(ccxt, settings.PAPER_EXCHANGE)
            self.exchange = exchange_cls(exchange_opts)
            if settings.PAPER_TESTNET:
                try:
                    self.exchange.set_sandbox_mode(True)
                except AttributeError:
                    self.exchange.urls["api"] = {
                        "spot": "https://api-testnet.bybit.com",
                        "linear": "https://api-testnet.bybit.com",
                        "inverse": "https://api-testnet.bybit.com",
                    }
            try:
                await self.exchange.load_markets()
            except Exception as lm_e:
                logger.warning(f"[{INSTANCE_ID}] Markets load failed ({lm_e}), continuing without market metadata")
            logger.info(f"[{INSTANCE_ID}] Connected to {settings.PAPER_EXCHANGE} testnet")
            self.use_testnet = True
            if SWAP_ENABLED:
                await self._init_leverage()
        except Exception as e:
            logger.warning(f"[{INSTANCE_ID}] Testnet connection failed: {e}, using in-memory simulation")
            if self.exchange:
                await self.exchange.close()
            self.exchange = None

    async def _init_leverage(self):
        if not self.exchange:
            return
        try:
            for sym in settings.TOP_PAIRS:
                swap_sym = self._swap_symbol(sym)
                if swap_sym != sym:
                    await self.exchange.set_leverage(LEVERAGE, swap_sym)
                    logger.info(f"[{INSTANCE_ID}] Set {LEVERAGE}x leverage on {swap_sym}")
        except Exception as e:
            logger.warning(f"[{INSTANCE_ID}] Leverage init warning: {e}")

    def _swap_symbol(self, symbol: str) -> str:
        if not SWAP_ENABLED or not self.exchange:
            return symbol
        swap_sym = f"{symbol}:USDT"
        if swap_sym in getattr(self.exchange, 'markets', {}):
            return swap_sym
        return symbol

    def _get_slippage_rate(self, symbol: str) -> float:
        return settings.SLIPPAGE_PCT_BY_SYMBOL.get(symbol, settings.SLIPPAGE_DEFAULT_ALT)

    def _get_fee_rate(self) -> float:
        return settings.TRADING_FEE_PERP_PCT if SWAP_ENABLED else settings.TRADING_FEE_SPOT_PCT

    async def _fetch_testnet_balance(self) -> float:
        if not self.exchange:
            return INITIAL_CAPITAL
        try:
            bal = await self.exchange.fetch_balance()
            total = float(bal.get("USDT", {}).get("total", 0) or 0)
            free = float(bal.get("USDT", {}).get("free", 0) or 0)
            if total > 0:
                logger.info(f"[{INSTANCE_ID}] Testnet balance: ${total:.2f} (free: ${free:.2f})")
                return total
        except Exception as e:
            logger.warning(f"[{INSTANCE_ID}] Could not fetch testnet balance: {e}")
        return INITIAL_CAPITAL

    async def _place_testnet_order(self, symbol: str, side: str, quantity: float,
                                    stop_loss: float | None = None, take_profit: float | None = None) -> dict | None:
        if not self.exchange:
            return None
        try:
            trade_symbol = self._swap_symbol(symbol)
            final_qty = quantity
            if SWAP_ENABLED and trade_symbol != symbol:
                try:
                    market = self.exchange.market(trade_symbol)
                    contract_size = float(market.get('contractSize', 1))
                    final_qty = max(1, round(quantity / contract_size))
                    quantity = final_qty * contract_size
                except Exception:
                    pass

            params = {}
            if take_profit:
                params["tpTriggerPx"] = take_profit
                params["tpOrdPx"] = -1
            if stop_loss:
                params["slTriggerPx"] = stop_loss
                params["slOrdPx"] = -1
            order = await self.exchange.create_order(trade_symbol, "market", side, final_qty, params)
            await asyncio.sleep(1)
            try:
                filled = await self.exchange.fetch_order(order["id"], trade_symbol, params={"acknowledged": True})
            except Exception:
                filled = await self.exchange.fetch_order(order["id"], trade_symbol)
            avg_price = float(filled.get("average", filled.get("price", 0) or 0))
            filled_qty = float(filled.get("filled", final_qty))
            if SWAP_ENABLED and trade_symbol != symbol:
                try:
                    market = self.exchange.market(trade_symbol)
                    contract_size = float(market.get('contractSize', 1))
                    filled_qty = filled_qty * contract_size
                except Exception:
                    pass
            fee_info = filled.get("fee", {})
            order_fee = float(fee_info["cost"]) if fee_info and fee_info.get("cost") else 0.0

            if avg_price > 0 and filled_qty > 0:
                logger.info(f"[{INSTANCE_ID}] Testnet fill: {side} {filled_qty:.6f} {symbol} @ ${avg_price:.4f} fee=${order_fee:.4f}")
                return {"price": avg_price, "quantity": filled_qty, "order_id": filled["id"], "fee": order_fee}
        except Exception as e:
            logger.warning(f"[{INSTANCE_ID}] Testnet order failed: {e}")
        return None

    async def _reconcile_positions(self):
        if not self.use_testnet or not self.exchange:
            return 0, 0, 0

        try:
            rows = await self.db.fetch(
                "SELECT order_id, symbol, side, entry_price, quantity, quantity_usd, "
                "stop_loss, take_profit, strategy, confidence, reasoning, time as opened_at "
                "FROM trades WHERE venue = $1 AND status = 'open' "
                "ORDER BY time DESC",
                VENUE,
            )
        except Exception as e:
            logger.warning(f"[{INSTANCE_ID}] Could not query open positions from DB: {e}")
            return 0, 0, 0

        if not rows:
            return 0, 0, 0

        logger.info(f"[{INSTANCE_ID}] Reconciliación: {len(rows)} posiciones 'open' encontradas en DB para venue={VENUE}")

        recovered = 0
        ghost_closed = 0
        unverified = 0

        for row in rows:
            order_id = row["order_id"]
            symbol = row["symbol"]
            side = row["side"]
            swap_sym = self._swap_symbol(symbol)

            still_open = False
            try:
                if SWAP_ENABLED:
                    all_positions = await self.exchange.fetch_positions()
                    still_open = any(
                        p.get("symbol") == swap_sym
                        and abs(float(p.get("contracts", 0) or 0)) > 0
                        for p in all_positions
                    )
                else:
                    open_orders = await self.exchange.fetch_open_orders(symbol)
                    still_open = len(open_orders) > 0
            except Exception as e:
                logger.error(
                    f"[{INSTANCE_ID}] API check failed for {symbol} order {order_id}: {e}. "
                    f"Marcando como open_unverified para revisión manual."
                )
                unverified += 1
                try:
                    await self.db.execute(
                        "UPDATE trades SET status = 'open_unverified' "
                        "WHERE order_id = $1 AND venue = $2",
                        order_id, VENUE,
                    )
                except Exception:
                    pass
                continue

            if still_open:
                entry_price = float(row["entry_price"])
                quantity = float(row["quantity"])
                quantity_usd = float(row["quantity_usd"])

                self.portfolio.positions[order_id] = {
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "entry_price": entry_price,
                    "quantity_usd": quantity_usd,
                    "stop_loss": row.get("stop_loss"),
                    "take_profit": row.get("take_profit"),
                    "strategy": row.get("strategy", ""),
                    "confidence": float(row.get("confidence", 0) or 0),
                    "reasoning": row.get("reasoning", ""),
                    "opened_at": row["opened_at"].isoformat() if hasattr(row["opened_at"], "isoformat") else str(row["opened_at"]),
                }
                self.portfolio.cash -= quantity_usd
                recovered += 1
                logger.info(f"[{INSTANCE_ID}] RECUPERADA posición {order_id}: {side} {symbol} ${quantity_usd:.2f}")
            else:
                await self.db.execute(
                    "UPDATE trades SET status = 'ghost_closed', closed_reason = 'reconciled_on_startup' "
                    "WHERE order_id = $1 AND venue = $2",
                    order_id, VENUE,
                )
                ghost_closed += 1
                logger.info(f"[{INSTANCE_ID}] GHOST_CLOSED posición {order_id}: {side} {symbol}")

        return recovered, ghost_closed, unverified

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        await self._init_exchange()
        testnet_balance = await self._fetch_testnet_balance()
        effective_capital = INITIAL_CAPITAL if INITIAL_CAPITAL > 0 else testnet_balance
        self.portfolio = Portfolio(effective_capital, settings.BASE_CURRENCY)

        rec_recovered, rec_ghost, rec_unverified = await self._reconcile_positions()

        if self.use_testnet and self.exchange:
            await self.redis.set_json(STATE_KEY, {
                "cash": self.portfolio.cash, "positions": self.portfolio.positions,
                "total_fees": 0, "total_slippage": 0,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        else:
            await self.load_state()

        local_limit = MAX_POSITIONS_BY_VENUE.get(VENUE, 10)
        logger.info(
            f"[{INSTANCE_ID}] Paper Trading initialized: capital=${self.portfolio.cash:.2f}, "
            f"strategy_filter={STRATEGY_FILTER or 'all'}, min_conf={MIN_CONFIDENCE}, "
            f"max_pos={MAX_POSITION_PCT*100:.0f}%, local_limit={local_limit}"
        )
        if rec_recovered > 0 or rec_ghost > 0 or rec_unverified > 0:
            logger.info(
                f"[{INSTANCE_ID}] Reconciliación completa: {rec_recovered} posiciones recuperadas, "
                f"{rec_ghost} marcadas ghost_closed, {rec_unverified} sin verificar, "
                f"arrancando con {len(self.portfolio.positions)} posiciones activas"
            )
        await self.redis.set_json(f"portfolio:initial_capital:{VENUE}", testnet_balance if self.use_testnet else effective_capital)

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

            venue_used = VENUE
            if is_close:
                matching_positions = [
                    oid for oid, pos in self.portfolio.positions.items()
                    if isinstance(pos, dict) and pos.get("symbol") == order.symbol
                ]
                if not matching_positions:
                    return

                for oid in matching_positions:
                    pos = self.portfolio.positions.get(oid)
                    if pos and self.use_testnet:
                        close_side = "buy" if pos["side"] == "sell" else "sell"
                        testnet = await self._place_testnet_order(order.symbol, close_side, pos["quantity"])
                        if testnet:
                            order.entry_price = testnet["price"]
                            venue_used = VENUE
                        else:
                            venue_used = "paper"
                    funding_charge = 0.0
                    if pos and SWAP_ENABLED:
                        opened_at = datetime.fromisoformat(pos.get("opened_at", datetime.now(timezone.utc).isoformat()))
                        hours_open = (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600
                        if hours_open > 8:
                            notional = pos.get("margin", 0) * pos.get("leverage", 1)
                            funding_charge = notional * settings.FUNDING_RATE_8H * (hours_open / 8)
                            logger.info(f"[{INSTANCE_ID}] Funding charge for {order.symbol}: ${funding_charge:.4f} ({hours_open:.1f}h open)")
                    result = self.portfolio.close_position(
                        oid, order.entry_price, reason=order.reasoning,
                        funding_charge=funding_charge,
                    )
                    if result:
                        result["signal_id"] = order.signal_id
                        result["venue"] = venue_used
                        await self.publish_result(result)
                        await self.store_trade(result)
            else:
                local_limit = MAX_POSITIONS_BY_VENUE.get(VENUE, 10)
                if len(self.portfolio.positions) >= local_limit:
                    logger.info(
                        f"[{INSTANCE_ID}] Rechazado: límite local de venue alcanzado "
                        f"({len(self.portfolio.positions)}/{local_limit}) para {order.symbol}"
                    )
                    return

                fee_rate = self._get_fee_rate()
                slippage_rate = self._get_slippage_rate(order.symbol)
                if self.use_testnet:
                    exchange_side = order.side.value
                    testnet = await self._place_testnet_order(
                        order.symbol, exchange_side, order.quantity,
                        order.stop_loss, order.take_profit,
                    )
                    if testnet:
                        order.entry_price = testnet["price"]
                        order.quantity = testnet["quantity"]
                        order.quantity_usd = testnet["price"] * testnet["quantity"]
                        venue_used = VENUE
                        notional = order.quantity_usd * LEVERAGE
                        if testnet["fee"] > 0 and notional > 0:
                            implied_fee_rate = testnet["fee"] / notional
                            if implied_fee_rate < 0.01:
                                fee_rate = implied_fee_rate
                            else:
                                logger.warning(
                                    f"[{INSTANCE_ID}] Testnet fee ${testnet['fee']:.4f} "
                                    f"implies {implied_fee_rate:.4%} rate for ${notional:.2f} notional, "
                                    f"using configured {fee_rate:.4%} instead"
                                )
                    else:
                        venue_used = "paper"
                else:
                    venue_used = "paper"
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
                    leverage=LEVERAGE,
                    fee_rate=fee_rate,
                    slippage_rate=slippage_rate,
                )
                if result:
                    result["signal_id"] = order.signal_id
                    result["venue"] = venue_used
                    await self.publish_result(result)
                    await self.store_trade(result)
                    self.trades_today += 1

            await self.save_state()
            stats = self.portfolio.get_stats(self.current_prices)
            await self.redis.set_json(STATS_KEY, stats)
            await self.redis.set_json(f"portfolio:stats:{venue_used}", stats)
            await self._update_strategy_signals(result)
            await self._update_strategy_metrics(result)
            logger.info(
                f"[{INSTANCE_ID}] value=${stats['total_value']:.2f} "
                f"PnL=${stats['total_pnl']:.2f} ({stats['total_pnl_pct']:.1f}%) "
                f"trades={stats['total_trades']} wr={stats['win_rate']:.0f}% "
                f"venue={venue_used}"
            )

        except Exception as e:
            logger.error(f"[{INSTANCE_ID}] Error executing order: {e}")

    async def publish_result(self, result: dict):
        trade_result = TradeResult(
            order_id=result["order_id"],
            signal_id=result.get("signal_id", ""),
            symbol=result["symbol"],
            side=SignalType(result["side"]),
            entry_price=result["entry_price"],
            exit_price=result.get("exit_price", result["entry_price"]),
            quantity=result["quantity"],
            quantity_usd=result["quantity_usd"],
            fee_usd=result["fee_usd"],
            slippage_usd=result.get("slippage_usd", 0),
            funding_usd=result.get("funding_usd", 0),
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
            if result.get("status") == "closed":
                await self.db.execute(
                    """UPDATE trades SET exit_price=$1, fee_usd=$2, slippage_usd=$3,
                       funding_usd=$4, pnl_usd=$5, status='closed', closed_reason=$6,
                       reasoning=$7, signal_id=$8, venue=$9
                       WHERE order_id=$10 AND status='open'""",
                    result.get("exit_price", result["entry_price"]),
                    result["fee_usd"], result.get("slippage_usd", 0),
                    result.get("funding_usd", 0), result["pnl_usd"],
                    result.get("reasoning", "Closed position")[:200],
                    result.get("reasoning", "")[:500],
                    result.get("signal_id", ""), result.get("venue", "paper"),
                    result["order_id"],
                )
            else:
                await self.db.execute(
                    """INSERT INTO trades (time, order_id, signal_id, symbol, side, entry_price, exit_price,
                       quantity, quantity_usd, fee_usd, slippage_usd, funding_usd, pnl_usd,
                       status, strategy, confidence, reasoning,
                       stop_loss, take_profit, venue)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                       $14, $15, $16, $17, $18, $19, $20)""",
                    datetime.now(timezone.utc), result["order_id"], result.get("signal_id", ""),
                    result["symbol"],
                    result["side"], result["entry_price"], result.get("exit_price", result["entry_price"]),
                    result["quantity"], result["quantity_usd"], result["fee_usd"],
                    result.get("slippage_usd", 0), result.get("funding_usd", 0),
                    result["pnl_usd"], result["status"], result.get("strategy", ""),
                    result.get("confidence", 0), result.get("reasoning", ""),
                    result.get("stop_loss"), result.get("take_profit"),
                    result.get("venue", "paper"),
                )
        except Exception as e:
            logger.error(f"[{INSTANCE_ID}] Error storing trade: {e}")

    async def _update_strategy_signals(self, result: dict):
        try:
            signals = await self.redis.get_json("strategy:latest_signals") or []
            signal_entry = {
                "signal_id": result.get("signal_id", ""),
                "symbol": result["symbol"],
                "side": result["side"],
                "strategy": result.get("strategy", ""),
                "confidence": result.get("confidence", 0),
                "pnl_usd": result["pnl_usd"],
                "entry_price": result["entry_price"],
                "exit_price": result.get("exit_price"),
                "reasoning": result.get("reasoning", ""),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            signals.append(signal_entry)
            if len(signals) > 50:
                signals = signals[-50:]
            await self.redis.set_json("strategy:latest_signals", signals)
        except Exception as e:
            logger.warning(f"Failed to update strategy signals: {e}")

    async def _update_strategy_metrics(self, result: dict):
        try:
            metrics = await self.redis.get_json("strategy:metrics") or {}
            strategy = result.get("strategy", "unknown")
            if strategy not in metrics:
                metrics[strategy] = {"trades": 0, "wins": 0, "losses": 0, "total_pnl": 0.0, "total_confidence": 0.0}
            m = metrics[strategy]
            m["trades"] += 1
            m["total_pnl"] += result["pnl_usd"]
            m["total_confidence"] += result.get("confidence", 0)
            if result["pnl_usd"] > 0:
                m["wins"] += 1
            elif result["pnl_usd"] < 0:
                m["losses"] += 1
            m["win_rate"] = round(m["wins"] / m["trades"] * 100, 1) if m["trades"] > 0 else 0
            m["avg_pnl"] = round(m["total_pnl"] / m["trades"], 2) if m["trades"] > 0 else 0
            m["avg_confidence"] = round(m["total_confidence"] / m["trades"], 3) if m["trades"] > 0 else 0
            m["updated_at"] = datetime.now(timezone.utc).isoformat()
            await self.redis.set_json("strategy:metrics", metrics)
        except Exception as e:
            logger.warning(f"Failed to update strategy metrics: {e}")

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
        while True:
            try:
                await self.initialize()
                break
            except Exception as e:
                logger.error(f"[{INSTANCE_ID}] Startup error (retrying in 5s): {e}")
                await asyncio.sleep(5)
        order_group = f"paper-trading-orders-{INSTANCE_ID}"
        order_consumer = f"paper-trading-{INSTANCE_ID}"
        market_group = f"paper-trading-market-{INSTANCE_ID}"
        market_consumer = f"paper-trading-market-{INSTANCE_ID}"

        stats_task = asyncio.create_task(self.periodic_stats())

        logger.info(f"[{INSTANCE_ID}] Paper Trading Engine running")
        while self.running:
            try:
                await self.redis.heartbeat("paper-trading")
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
        if self.exchange:
            await self.exchange.close()


async def main():
    engine = PaperTradingEngine()
    await engine.run()


if __name__ == "__main__":
    asyncio.run(main())
