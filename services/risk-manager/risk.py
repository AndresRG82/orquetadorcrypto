import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.models import TradingSignal, RiskAssessment, SignalType
from shared.db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("risk-manager")

MAX_CONCURRENT_POSITIONS = 10
SIGNAL_COOLDOWNS: dict[str, dict] = {
    "scalping": {"seconds": 900, "label": "15min"},
    "swing": {"seconds": 3600, "label": "1h"},
    "arbitrage": {"seconds": 7200, "label": "2h"},
    "qwen_direct": {"seconds": 1800, "label": "30min"},
}
DEFAULT_COOLDOWN = 1800


class RiskManager:
    MAX_POSITION_PCT = float(settings.MAX_POSITION_PCT)
    MAX_DRAWDOWN_PCT = float(settings.MAX_DRAWDOWN_PCT)
    INITIAL_CAPITAL = float(settings.INITIAL_CAPITAL)

    LOSS_COOLDOWNS = [
        (3, 900),
        (5, 1800),
        (7, 3600),
    ]

    def __init__(self):
        self.redis: RedisClient | None = None
        self.running = False
        self.portfolio_value: float = self.INITIAL_CAPITAL
        self.peak_value: float = self.INITIAL_CAPITAL
        self.cash_available: float = self.INITIAL_CAPITAL
        self.open_positions: dict[str, dict] = {}
        self.consecutive_losses: int = 0
        self.loss_cooldown_until: float = 0.0
        self.total_trades: int = 0
        self.winning_trades: int = 0
        self.signal_cooldowns: dict[str, datetime] = {}
        self.evo_params: dict = {}

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        await self.load_evo_params()
        await self.load_portfolio_state()
        logger.info(
            f"Risk Manager initialized: max_position={self.MAX_POSITION_PCT*100:.0f}%, "
            f"max_drawdown={self.MAX_DRAWDOWN_PCT*100:.0f}%, capital=${self.INITIAL_CAPITAL}, "
            f"max_positions={MAX_CONCURRENT_POSITIONS}"
        )

    async def load_evo_params(self):
        stored = await self.redis.get_json("risk:params")
        defaults = {
            "max_position_pct": self.MAX_POSITION_PCT,
            "max_drawdown_pct": self.MAX_DRAWDOWN_PCT,
            "kelly_fraction": 0.5,
            "min_confidence": 0.5,
            "min_risk_reward": 1.5,
            "stop_loss_atr_mult": 1.0,
            "take_profit_atr_mult": 3.0,
        }
        self.evo_params = {**defaults, **(stored or {})}
        self.MAX_POSITION_PCT = float(self.evo_params["max_position_pct"])
        self.MAX_DRAWDOWN_PCT = float(self.evo_params["max_drawdown_pct"])
        logger.info(f"Risk evo params loaded: max_pos={self.MAX_POSITION_PCT:.2f} max_dd={self.MAX_DRAWDOWN_PCT:.2f} kelly={self.evo_params['kelly_fraction']}")

    async def load_portfolio_state(self):
        try:
            own_state = await self.redis.get_json("portfolio:state")
            if own_state:
                self.portfolio_value = float(own_state.get("total_value", self.INITIAL_CAPITAL))
                loaded_peak = float(own_state.get("peak_value", self.portfolio_value))
                if loaded_peak > self.portfolio_value * 1.05:
                    logger.warning(f"Capping peak_value from {loaded_peak:.2f} to {self.portfolio_value:.2f} (was {((loaded_peak/self.portfolio_value)-1)*100:.1f}% above current)")
                    self.peak_value = self.portfolio_value
                else:
                    self.peak_value = loaded_peak
                self.open_positions = own_state.get("positions", {})
                self.consecutive_losses = int(own_state.get("consecutive_losses", 0))
                self.loss_cooldown_until = float(own_state.get("loss_cooldown_until", 0))
                self.total_trades = int(own_state.get("total_trades", 0))
                self.winning_trades = int(own_state.get("winning_trades", 0))
                logger.info(f"Loaded own state: value=${self.portfolio_value:.2f}, positions={len(self.open_positions)}")

            pt_state = await self.redis.get_json("paper_trading:state")
            if pt_state:
                pt_cash = float(pt_state.get("cash", self.INITIAL_CAPITAL))
                pt_positions = pt_state.get("positions", {})
                total_position_value = sum(
                    float(p.get("quantity", 0)) * float(p.get("entry_price", 0))
                    for p in pt_positions.values() if isinstance(p, dict)
                )
                real_value = pt_cash + total_position_value
                logger.info(f"Paper trading state: cash=${pt_cash:.2f}, positions={len(pt_positions)}, value=${real_value:.2f}")

                if not own_state or abs(self.portfolio_value - real_value) > real_value * 0.5:
                    logger.info(f"Syncing portfolio value from paper trading: ${real_value:.2f}")
                    self.portfolio_value = real_value
                    self.peak_value = max(self.peak_value, real_value)
                    self.cash_available = pt_cash

                if not own_state:
                    self.open_positions = {
                        oid: {
                            "symbol": p.get("symbol", ""),
                            "side": p.get("side", ""),
                            "quantity_usd": float(p.get("quantity_usd", 0)),
                        }
                        for oid, p in pt_positions.items() if isinstance(p, dict)
                    }

            logger.info(f"Portfolio: value=${self.portfolio_value:.2f}, cash=${self.cash_available:.2f}, positions={len(self.open_positions)}")
        except Exception as e:
            logger.warning(f"Could not load portfolio state: {e}")

    async def sync_from_paper_trading(self):
        try:
            stats = await self.redis.get_json("portfolio:stats")
            if stats:
                self.portfolio_value = float(stats.get("total_value", self.portfolio_value))
                self.cash_available = float(stats.get("cash", self.cash_available))

            pt_state = await self.redis.get_json("paper_trading:state")
            if pt_state and pt_state.get("positions"):
                self.open_positions = {
                    oid: {
                        "symbol": p.get("symbol", ""),
                        "side": p.get("side", ""),
                        "quantity_usd": float(p.get("quantity_usd", 0)),
                    }
                    for oid, p in pt_state["positions"].items() if isinstance(p, dict)
                }
                self.cash_available = float(pt_state.get("cash", self.cash_available))
        except Exception:
            pass

    async def save_portfolio_state(self):
        state = {
            "total_value": self.portfolio_value,
            "peak_value": self.peak_value,
            "positions": self.open_positions,
            "consecutive_losses": self.consecutive_losses,
            "loss_cooldown_until": self.loss_cooldown_until,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "cash_available": self.cash_available,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await self.redis.set_json("portfolio:state", state)

    async def save_signal(self, signal: TradingSignal, assessment: RiskAssessment):
        try:
            await self.db.execute(
                """INSERT INTO signals (time, signal_id, symbol, timeframe, signal, confidence, strategy, reasoning, entry_price, target_price, stop_loss, approved)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
                signal.timestamp,
                signal.signal_id,
                signal.symbol,
                signal.timeframe,
                signal.signal.value,
                signal.confidence,
                signal.strategy,
                assessment.reason,
                signal.entry_price,
                signal.target_price,
                signal.stop_loss,
                assessment.approved,
            )
        except Exception as e:
            logger.warning(f"Failed to save signal to DB: {e}")

    def is_loss_cooldown_active(self) -> bool:
        return time.time() < self.loss_cooldown_until

    def get_loss_cooldown_seconds(self) -> int:
        for threshold, seconds in reversed(self.LOSS_COOLDOWNS):
            if self.consecutive_losses >= threshold:
                return seconds
        return 0

    def calculate_position_size(self, signal: TradingSignal, position_multiplier: float = 1.0) -> float:
        base_size = self.portfolio_value * self.MAX_POSITION_PCT

        confidence_mult = signal.confidence

        if self.consecutive_losses >= 5:
            loss_mult = 0.5
        elif self.consecutive_losses >= 3:
            loss_mult = 0.75
        else:
            loss_mult = 1.0

        position_size = base_size * confidence_mult * loss_mult * position_multiplier

        max_by_cash = self.cash_available * 0.8
        position_size = min(position_size, max_by_cash)

        max_global = self.portfolio_value * 0.15
        position_size = min(position_size, max_global)

        min_size = 5.0
        position_size = max(position_size, min_size)

        return round(position_size, 2)

    def check_drawdown(self) -> tuple[bool, float]:
        if self.portfolio_value > self.peak_value:
            self.peak_value = self.portfolio_value

        if self.peak_value == 0:
            return False, 0.0

        drawdown = (self.peak_value - self.portfolio_value) / self.peak_value
        return drawdown > self.MAX_DRAWDOWN_PCT, drawdown

    def check_correlation(self, signal: TradingSignal) -> bool:
        same_base = sum(
            1 for p in self.open_positions.values()
            if p.get("symbol", "").split("/")[0] == signal.symbol.split("/")[0]
        )
        return same_base < 1

    def check_cooldown(self, signal: TradingSignal) -> bool:
        key = signal.symbol
        if key in self.signal_cooldowns:
            cooldown_until = self.signal_cooldowns[key]
            if datetime.now(timezone.utc) < cooldown_until:
                return False
        return True

    def set_cooldown(self, signal: TradingSignal):
        strategy = signal.strategy or "default"
        cooldown_cfg = SIGNAL_COOLDOWNS.get(strategy, SIGNAL_COOLDOWNS.get("qwen_direct", {"seconds": DEFAULT_COOLDOWN}))
        cooldown_seconds = cooldown_cfg["seconds"]
        self.signal_cooldowns[signal.symbol] = datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)

    async def evaluate_signal(self, signal: TradingSignal, position_multiplier: float = 1.0) -> RiskAssessment:
        reasons = []
        approved = True

        circuit_data = await self.redis.get_json("circuit:state") if self.redis else None
        if circuit_data:
            if circuit_data.get("status") == "tripped":
                resume_at = circuit_data.get("resume_at", "")
                if resume_at > datetime.now(timezone.utc).isoformat():
                    if signal.confidence >= 0.90:
                        position_multiplier *= 0.5
                        reasons.append(f"Circuit breaker bypass (confidence {signal.confidence:.0%} >= 90%, size=50%)")
                    else:
                        approved = False
                        reasons.append(f"Circuit breaker active: {circuit_data.get('reason')} (resumes {resume_at})")

        has_position_in_symbol = any(
            p.get("symbol") == signal.symbol for p in self.open_positions.values()
        )
        is_closing = has_position_in_symbol and signal.signal == SignalType.SELL

        if len(self.open_positions) >= MAX_CONCURRENT_POSITIONS and not is_closing:
            approved = False
            reasons.append(f"Max positions reached ({len(self.open_positions)}/{MAX_CONCURRENT_POSITIONS})")

        max_dd, current_dd = self.check_drawdown()
        if max_dd:
            approved = False
            reasons.append(f"Max drawdown exceeded ({current_dd*100:.1f}%)")
        else:
            reasons.append(f"Drawdown OK ({current_dd*100:.1f}%)")

        if not is_closing and not self.check_correlation(signal):
            approved = False
            reasons.append(f"Already have position in {signal.symbol.split('/')[0]}")
        else:
            reasons.append("Correlation check passed")

        excluded = await self.redis.get_json("risk:excluded_symbols") if self.redis else None
        if excluded and not is_closing:
            if signal.symbol in excluded:
                approved = False
                reasons.append(f"Symbol {signal.symbol} dynamically excluded")

        if not self.check_cooldown(signal):
            approved = False
            cooldown_cfg = SIGNAL_COOLDOWNS.get(signal.strategy, {})
            reasons.append(f"Cooldown active for {signal.symbol} ({cooldown_cfg.get('label', '30min')})")
        else:
            reasons.append("Cooldown OK")

        if signal.confidence < self.evo_params["min_confidence"]:
            approved = False
            reasons.append(f"Low confidence ({signal.confidence:.2f}, min={self.evo_params['min_confidence']:.2f})")
        else:
            reasons.append(f"Confidence OK ({signal.confidence:.2f})")

        if not is_closing:
            for pos_id, pos in self.open_positions.items():
                if pos.get("symbol") == signal.symbol:
                    approved = False
                    reasons.append(f"Already have position on {signal.symbol}")
                    break

        high_confidence_bypass = False
        if self.is_loss_cooldown_active() and not is_closing:
            remaining = int(self.loss_cooldown_until - time.time())
            if self.consecutive_losses == 0:
                self.loss_cooldown_until = 0
            elif signal.confidence >= 0.90:
                high_confidence_bypass = True
                position_multiplier *= 0.5
                reasons.append(f"Loss cooldown bypass (confidence {signal.confidence:.0%} >= 90%, size=50%)")
            else:
                approved = False
                reasons.append(f"Loss cooldown active ({remaining}s left, {self.consecutive_losses} consecutive losses)")

        position_size = self.calculate_position_size(signal, position_multiplier) if approved else 0.0

        if high_confidence_bypass:
            position_size *= 0.5

        if approved and not is_closing and position_size > self.cash_available:
            approved = False
            reasons.append(f"Insufficient cash (${self.cash_available:.2f} < ${position_size:.2f})")

        risk_reward = None
        adjusted_stop = None
        if signal.stop_loss and signal.target_price and signal.entry_price:
            rr = abs(signal.target_price - signal.entry_price) / abs(signal.stop_loss - signal.entry_price)
            risk_reward = round(rr, 2)
            if rr < self.evo_params["min_risk_reward"]:
                approved = False
                reasons.append(f"Risk/Reward {rr:.2f} < {self.evo_params['min_risk_reward']:.1f} - rejected")
            else:
                reasons.append(f"Risk/Reward {rr:.2f} OK")

        if not approved and not reasons:
            reasons.append("Signal rejected")

        reason = "; ".join(reasons)

        assessment = RiskAssessment(
            signal_id=signal.signal_id,
            approved=approved,
            reason=reason,
            position_size_usd=position_size if approved else None,
            adjusted_stop_loss=adjusted_stop,
            risk_reward_ratio=risk_reward,
            portfolio_risk_pct=current_dd,
        )

        asyncio.create_task(self.save_signal(signal, assessment))

        return assessment

    async def process_signal(self, data: dict):
        try:
            signal = TradingSignal(**data)
            position_multiplier = float(data.get("position_multiplier", 1.0))
            strategy_active = data.get("strategy_active", True)

            if not strategy_active:
                logger.info(f"SKIPPED: Strategy {signal.strategy} deactivated, dropping signal for {signal.symbol}")
                return

            assessment = await self.evaluate_signal(signal, position_multiplier)

            stream = settings.STREAM_RISK_APPROVED if assessment.approved else "risk:rejected"
            await self.redis.publish(stream, assessment.model_dump(mode="json"))

            if assessment.approved:
                self.set_cooldown(signal)
                logger.info(
                    f"APPROVED: {signal.signal.value} {signal.symbol} {signal.timeframe} "
                    f"pos=${assessment.position_size_usd:.2f} RR={assessment.risk_reward_ratio} "
                    f"reason={assessment.reason[:120]}"
                )
            else:
                logger.info(
                    f"REJECTED: {signal.signal.value} {signal.symbol} {signal.timeframe} "
                    f"reason={assessment.reason[:120]}"
                )

        except Exception as e:
            logger.error(f"Error processing signal: {e}")

    async def update_from_trade_results(self, data: dict):
        try:
            status = str(data.get("status", ""))
            pnl = float(data.get("pnl_usd", 0))

            if status == "closed":
                self.total_trades += 1
                if pnl > 0:
                    self.winning_trades += 1
                    self.consecutive_losses = 0
                    self.portfolio_value += pnl
                    self.cash_available += float(data.get("quantity_usd", 0)) + pnl
                else:
                    self.consecutive_losses += 1
                    cooldown = self.get_loss_cooldown_seconds()
                    if cooldown > 0:
                        self.loss_cooldown_until = time.time() + cooldown
                        logger.info(
                            f"Loss streak: {self.consecutive_losses} consecutive, "
                            f"cooldown {cooldown}s"
                        )
                    self.portfolio_value += pnl
                    self.cash_available += float(data.get("quantity_usd", 0)) + pnl

                order_id = data.get("order_id", "")
                if order_id in self.open_positions:
                    del self.open_positions[order_id]

                symbol = data.get("symbol", "")
                if symbol in self.signal_cooldowns:
                    del self.signal_cooldowns[symbol]

                await self.save_portfolio_state()

            elif status == "open":
                order_id = data.get("order_id", "")
                symbol = data.get("symbol", "")
                side = data.get("side", "")
                quantity_usd = float(data.get("quantity_usd", 0))
                self.open_positions[order_id] = {
                    "symbol": symbol,
                    "side": side,
                    "quantity_usd": quantity_usd,
                }
                self.cash_available -= quantity_usd
                await self.save_portfolio_state()

        except Exception as e:
            logger.error(f"Error updating from trade result: {e}")

    async def run(self):
        self.running = True
        await self.initialize()
        signal_group = "risk-manager-signals"
        signal_consumer = "risk-manager-1"
        results_group = "risk-manager-results"
        results_consumer = "risk-manager-results-1"
        sync_counter = 0

        logger.info("Risk Manager running")
        while self.running:
            try:
                await self.redis.heartbeat("risk-manager")
                messages = await self.redis.read_stream(
                    settings.STREAM_SIGNALS, signal_group, signal_consumer, count=10, block=2000,
                )
                for msg_id, data in messages:
                    await self.process_signal(data)

                results = await self.redis.read_stream(
                    settings.STREAM_TRADE_RESULTS, results_group, results_consumer, count=10, block=1000,
                )
                for msg_id, data in results:
                    await self.update_from_trade_results(data)

                sync_counter += 1
                if sync_counter % 15 == 0:
                    await self.sync_from_paper_trading()
                    await self.load_evo_params()

                expired = [k for k, v in self.signal_cooldowns.items() if datetime.now(timezone.utc) > v]
                for k in expired:
                    del self.signal_cooldowns[k]

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Risk Manager error: {e}")
                await asyncio.sleep(5)


async def main():
    manager = RiskManager()
    await manager.run()


if __name__ == "__main__":
    asyncio.run(main())