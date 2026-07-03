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

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("portfolio-tracker")


class Portfolio:
    def __init__(self, initial_capital: float, base_currency: str = "USDT"):
        self.initial_capital = initial_capital
        self.base_currency = base_currency
        self.cash = initial_capital
        self.positions: dict[str, dict] = {}
        self.trade_history: list[dict] = []
        self.total_fees = 0.0
        self.total_slippage = 0.0

    MIN_POSITION_USD = 5.0

    def open_position(self, order_id: str, symbol: str, side: str, quantity: float,
                      entry_price: float, quantity_usd: float, stop_loss: float = None,
                      take_profit: float = None, strategy: str = "", confidence: float = 0.0,
                      reasoning: str = "", leverage: float = 1.0,
                      fee_rate: float | None = None,
                      slippage_rate: float | None = None) -> Optional[dict]:
        margin = quantity_usd
        notional = margin * leverage
        _fee_rate = fee_rate if fee_rate is not None else float(settings.TRADING_FEE_PCT)
        _slippage_rate = slippage_rate if slippage_rate is not None else float(settings.SLIPPAGE_PCT)
        fee = notional * _fee_rate
        MAX_FEE_RATE = 0.01
        if _fee_rate > MAX_FEE_RATE:
            logger.warning(f"Fee rate {_fee_rate:.4%} > {MAX_FEE_RATE:.1%}, capping at configured max")
            _fee_rate = min(_fee_rate, float(settings.TRADING_FEE_PCT) or 0.001)
            fee = notional * _fee_rate
        slippage = notional * _slippage_rate
        effective_price = entry_price * (1 + _slippage_rate) if side == "buy" else entry_price * (1 - _slippage_rate)
        quantity = notional / effective_price

        cost = margin + fee + slippage

        if cost > self.cash:
            effective_margin = self.cash * 0.95
            if effective_margin < self.MIN_POSITION_USD:
                logger.warning(f"Insufficient cash (${self.cash:.2f}) for {symbol}, minimum ${self.MIN_POSITION_USD}")
                return None
            margin = effective_margin
            notional = margin * leverage
            quantity = notional / effective_price
            fee = notional * _fee_rate
            slippage = notional * _slippage_rate
            cost = margin + fee + slippage

        self.cash -= cost
        self.total_fees += fee
        self.total_slippage += slippage

        position = {
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "entry_price": effective_price,
            "quantity_usd": notional,
            "margin": margin,
            "leverage": leverage,
            "fee": fee,
            "slippage": slippage,
            "fee_rate": _fee_rate,
            "slippage_rate": _slippage_rate,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "strategy": strategy,
            "confidence": confidence,
            "reasoning": reasoning,
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        self.positions[order_id] = position

        result = {
            "order_id": order_id,
            "signal_id": "",
            "symbol": symbol,
            "side": side,
            "entry_price": effective_price,
            "exit_price": effective_price,
            "quantity": quantity,
            "quantity_usd": notional,
            "margin": margin,
            "leverage": leverage,
            "fee_usd": fee,
            "slippage_usd": slippage,
            "pnl_usd": -(fee + slippage),
            "status": "open",
            "strategy": strategy,
            "confidence": confidence,
            "reasoning": reasoning,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
        }

        leverage_str = f" {leverage}x" if leverage > 1 else ""
        logger.info(
            f"OPENED: {side.upper()} {symbol} qty={quantity:.6f} "
            f"@ ${effective_price:.2f} (${margin:.2f} margin{leverage_str}) fee=${fee:.4f}"
        )
        return result

    def close_position(self, order_id: str, close_price: float, reason: str = "",
                       funding_charge: float = 0.0) -> Optional[dict]:
        if order_id not in self.positions:
            logger.warning(f"Position not found: {order_id}")
            return None

        pos = self.positions[order_id]
        _slippage_rate = pos.get("slippage_rate") or float(settings.SLIPPAGE_PCT)
        _fee_rate = pos.get("fee_rate") or float(settings.TRADING_FEE_PCT)
        effective_close = close_price * (1 - _slippage_rate) if pos["side"] == "buy" else close_price * (1 + _slippage_rate)

        MAX_CLOSE_FEE_RATE = 0.01
        if _fee_rate > MAX_CLOSE_FEE_RATE:
            logger.warning(f"Close fee rate {_fee_rate:.4%} > {MAX_CLOSE_FEE_RATE:.1%}, capping")
            _fee_rate = float(settings.TRADING_FEE_PCT) or 0.001
        fee = pos["quantity"] * effective_close * _fee_rate
        slippage = abs(effective_close - close_price) * pos["quantity"]
        self.total_fees += fee
        self.total_slippage += slippage

        if pos["side"] == "buy":
            gross_pnl = (close_price - pos["entry_price"]) * pos["quantity"]
        else:
            gross_pnl = (pos["entry_price"] - close_price) * pos["quantity"]

        net_pnl = gross_pnl - fee - slippage - funding_charge
        leverage = pos.get("leverage") or 1.0
        margin = pos.get("margin") or pos["quantity_usd"]
        proceeds = margin + gross_pnl
        self.cash += proceeds - fee - slippage - funding_charge

        del self.positions[order_id]

        result = {
            "order_id": order_id,
            "symbol": pos["symbol"],
            "side": pos["side"],
            "entry_price": pos["entry_price"],
            "exit_price": effective_close,
            "quantity": pos["quantity"],
            "quantity_usd": pos["quantity_usd"],
            "margin": margin,
            "leverage": leverage,
            "fee_usd": fee,
            "slippage_usd": slippage,
            "funding_usd": funding_charge,
            "pnl_usd": net_pnl,
            "status": "closed",
            "strategy": pos.get("strategy", ""),
            "confidence": pos.get("confidence", 0),
            "reasoning": reason or f"Closed position",
            "stop_loss": pos.get("stop_loss"),
            "take_profit": pos.get("take_profit"),
        }

        self.trade_history.append(result)

        pnl_str = f"+${net_pnl:.2f}" if net_pnl > 0 else f"-${abs(net_pnl):.2f}"
        logger.info(
            f"CLOSED: {pos['side'].upper()} {pos['symbol']} "
            f"PnL={pnl_str} reason={reason}"
        )

        return result

    def get_portfolio_value(self, current_prices: dict[str, float] = None) -> float:
        total = self.cash
        for oid, pos in self.positions.items():
            symbol = pos["symbol"]
            price = current_prices.get(symbol, pos["entry_price"]) if current_prices else pos["entry_price"]
            total += pos["quantity"] * price
        return total

    def get_stats(self, current_prices: dict[str, float] = None) -> dict:
        total_value = self.get_portfolio_value(current_prices)
        total_pnl = total_value - self.initial_capital
        winning = [t for t in self.trade_history if t["pnl_usd"] > 0]
        losing = [t for t in self.trade_history if t["pnl_usd"] <= 0]

        return {
            "initial_capital": self.initial_capital,
            "total_value": total_value,
            "cash": self.cash,
            "total_pnl": total_pnl,
            "total_pnl_pct": (total_pnl / self.initial_capital) * 100 if self.initial_capital > 0 else 0,
            "open_positions": len(self.positions),
            "total_trades": len(self.trade_history),
            "winning_trades": len(winning),
            "losing_trades": len(losing),
            "win_rate": len(winning) / len(self.trade_history) * 100 if self.trade_history else 0,
            "total_fees": self.total_fees,
            "total_slippage": self.total_slippage,
        }