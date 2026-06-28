import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.models import TradingSignal, RiskAssessment, SignalType
from shared.db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("risk-manager")

MAX_POSITIONS_PAPER = int(os.getenv("MAX_POSITIONS_PAPER", "10"))
MAX_POSITIONS_OKX_SPOT = int(os.getenv("MAX_POSITIONS_OKX_SPOT", "6"))
MAX_POSITIONS_OKX_SWAP = int(os.getenv("MAX_POSITIONS_OKX_SWAP", "4"))
MAX_CONCURRENT_POSITIONS = MAX_POSITIONS_PAPER + MAX_POSITIONS_OKX_SPOT + MAX_POSITIONS_OKX_SWAP

VENUE_STATE_KEYS: dict[str, str] = {
    "paper": "paper_trading:state",
    "okx_testnet": "paper_trading:state:okx",
    "okx_swap_testnet": "paper_trading:state:okx_swap",
}
VENUE_MAX_POSITIONS: dict[str, int] = {
    "paper": MAX_POSITIONS_PAPER,
    "okx_testnet": MAX_POSITIONS_OKX_SPOT,
    "okx_swap_testnet": MAX_POSITIONS_OKX_SWAP,
}

SIGNAL_COOLDOWNS: dict[str, dict] = {
    "scalping": {"seconds": 900, "label": "15min"},
    "swing": {"seconds": 3600, "label": "1h"},
    "arbitrage": {"seconds": 7200, "label": "2h"},
    "qwen_direct": {"seconds": 1800, "label": "30min"},
}
DEFAULT_COOLDOWN = 1800

RISK_AUTOTUNE_ENABLED = os.getenv("RISK_AUTOTUNE_ENABLED", "false").lower() == "true"
RISK_AUTOTUNE_INTERVAL = int(os.getenv("RISK_AUTOTUNE_INTERVAL", "300"))
RISK_AUTOTUNE_MODEL = os.getenv("RISK_AUTOTUNE_MODEL", "qwen2.5:3b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")


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
        self.open_positions_by_venue: dict[str, dict[str, dict]] = {
            "paper": {}, "okx_testnet": {}, "okx_swap_testnet": {},
        }
        self.consecutive_losses: int = 0
        self.loss_cooldown_until: float = 0.0
        self.total_trades: int = 0
        self.winning_trades: int = 0
        self.signal_cooldowns: dict[str, datetime] = {}
        self.evo_params: dict = {}
        self.auto_tuner = RiskAutoTuner(self)
        self._startup_recovered = False

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        await self.load_evo_params()
        await self.load_portfolio_state()
        limits = ", ".join(f"{v}={MAX_POSITIONS_PAPER}" for v in ["paper"]) if False else \
            f"paper={MAX_POSITIONS_PAPER} okx_spot={MAX_POSITIONS_OKX_SPOT} okx_swap={MAX_POSITIONS_OKX_SWAP}"
        per_venue_counts = ", ".join(
            f"{v}={len(self.open_positions_by_venue[v])}"
            for v in ["paper", "okx_testnet", "okx_swap_testnet"]
        )
        logger.info(
            f"Risk Manager initialized: max_position={self.MAX_POSITION_PCT*100:.0f}%, "
            f"max_drawdown={self.MAX_DRAWDOWN_PCT*100:.0f}%, capital=${self.INITIAL_CAPITAL}, "
            f"limits: paper={MAX_POSITIONS_PAPER} okx_spot={MAX_POSITIONS_OKX_SPOT} okx_swap={MAX_POSITIONS_OKX_SWAP}, "
            f"total_max={MAX_CONCURRENT_POSITIONS}, "
            f"open_per_venue: {per_venue_counts}, combined={len(self.open_positions)}"
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
                self.consecutive_losses = int(own_state.get("consecutive_losses", 0))
                self.loss_cooldown_until = float(own_state.get("loss_cooldown_until", 0))
                self.total_trades = int(own_state.get("total_trades", 0))
                self.winning_trades = int(own_state.get("winning_trades", 0))
                logger.info(f"Loaded own state: value=${self.portfolio_value:.2f}, trades={self.total_trades}, consecutive_losses={self.consecutive_losses}")

            positions_from_any = False
            for venue_name, state_key in VENUE_STATE_KEYS.items():
                try:
                    vstate = await self.redis.get_json(state_key)
                    if vstate and vstate.get("positions"):
                        count = 0
                        for oid, pdata in vstate["positions"].items():
                            if isinstance(pdata, dict):
                                self.open_positions_by_venue[venue_name][oid] = {
                                    "symbol": pdata.get("symbol", ""),
                                    "side": pdata.get("side", ""),
                                    "quantity_usd": float(pdata.get("quantity_usd", 0)),
                                    "venue": venue_name,
                                }
                                count += 1
                        if count > 0:
                            positions_from_any = True
                            logger.info(f"Loaded {count} positions from {state_key} (venue={venue_name})")

                        if venue_name == "paper" and vstate.get("cash"):
                            self.cash_available = float(vstate["cash"])
                except Exception as ve:
                    logger.debug(f"Could not load from {state_key}: {ve}")
                await asyncio.sleep(0)

            self.open_positions = {}
            for venue_name, vpos in self.open_positions_by_venue.items():
                self.open_positions.update(vpos)

            total_recovered = len(self.open_positions)
            self._startup_recovered = total_recovered > 0 or self.total_trades > 0 or own_state is not None

            if self._startup_recovered:
                logger.info(
                    f"Estado recuperado de Redis: consecutive_losses={self.consecutive_losses}, "
                    f"total_trades={self.total_trades}, open_positions={total_recovered}"
                    f" ({', '.join(f'{v}={len(self.open_positions_by_venue[v])}' for v in VENUE_STATE_KEYS)})"
                )
            else:
                logger.info("Sin estado previo encontrado, iniciando desde cero")

        except Exception as e:
            logger.warning(f"Could not load portfolio state: {e}")

    async def sync_from_paper_trading(self):
        try:
            stats = await self.redis.get_json("portfolio:stats")
            if stats:
                self.portfolio_value = float(stats.get("total_value", self.portfolio_value))
                self.cash_available = float(stats.get("cash", self.cash_available))

            new_by_venue: dict[str, dict[str, dict]] = {
                "paper": {}, "okx_testnet": {}, "okx_swap_testnet": {},
            }
            total_loaded = 0
            for venue_name, state_key in VENUE_STATE_KEYS.items():
                try:
                    vstate = await self.redis.get_json(state_key)
                    if vstate and vstate.get("positions"):
                        for oid, pdata in vstate["positions"].items():
                            if isinstance(pdata, dict):
                                new_by_venue[venue_name][oid] = {
                                    "symbol": pdata.get("symbol", ""),
                                    "side": pdata.get("side", ""),
                                    "quantity_usd": float(pdata.get("quantity_usd", 0)),
                                    "venue": venue_name,
                                }
                                total_loaded += 1
                        if venue_name == "paper" and vstate.get("cash"):
                            self.cash_available = float(vstate["cash"])
                except Exception as ve:
                    logger.debug(f"Sync: could not read {state_key}: {ve}")

            self.open_positions_by_venue = new_by_venue
            self.open_positions = {}
            for vname, vpos in self.open_positions_by_venue.items():
                self.open_positions.update(vpos)
            logger.info(
                f"Synced positions from all venues: {total_loaded} total "
                f"({', '.join(f'{v}={len(new_by_venue[v])}' for v in VENUE_STATE_KEYS)})"
            )
        except Exception as e:
            logger.warning(f"Sync error: {e}")

    async def save_portfolio_state(self):
        state = {
            "total_value": self.portfolio_value,
            "peak_value": self.peak_value,
            "positions": self.open_positions,
            "positions_by_venue": {
                v: {oid: p for oid, p in vpos.items()}
                for v, vpos in self.open_positions_by_venue.items()
            },
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

        has_position_in_symbol = any(
            p.get("symbol") == signal.symbol for p in self.open_positions.values()
        )
        has_open_long = has_position_in_symbol and any(
            p.get("symbol") == signal.symbol and p.get("side") == "buy"
            for p in self.open_positions.values()
        )
        has_open_short = has_position_in_symbol and any(
            p.get("symbol") == signal.symbol and p.get("side") == "sell"
            for p in self.open_positions.values()
        )
        is_closing = (has_open_long and signal.signal == SignalType.SELL) or \
                     (has_open_short and signal.signal == SignalType.BUY)

        circuit_data = await self.redis.get_json("circuit:state") if self.redis else None
        if circuit_data:
            if circuit_data.get("status") == "tripped":
                resume_at = circuit_data.get("resume_at", "")
                if resume_at > datetime.now(timezone.utc).isoformat():
                    if is_closing:
                        reasons.append(f"Circuit breaker bypass (close signal for {signal.symbol})")
                    elif signal.confidence >= 0.90:
                        position_multiplier *= 0.5
                        reasons.append(f"Circuit breaker bypass (confidence {signal.confidence:.0%} >= 90%, size=50%)")
                    else:
                        approved = False
                        reasons.append(f"Circuit breaker active: {circuit_data.get('reason')} (resumes {resume_at})")

        total_positions = len(self.open_positions)
        venue_counts = {v: len(self.open_positions_by_venue[v]) for v in VENUE_STATE_KEYS}
        venue_limits_str = (
            f"paper={MAX_POSITIONS_PAPER}(cur={venue_counts['paper']}) "
            f"okx_spot={MAX_POSITIONS_OKX_SPOT}(cur={venue_counts['okx_testnet']}) "
            f"okx_swap={MAX_POSITIONS_OKX_SWAP}(cur={venue_counts['okx_swap_testnet']}) "
            f"total={total_positions}/{MAX_CONCURRENT_POSITIONS}"
        )

        if total_positions >= MAX_CONCURRENT_POSITIONS and not is_closing:
            approved = False
            reasons.append(f"Max positions reached ({venue_limits_str})")

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

    def _resolve_venue(self, data: dict) -> str:
        venue = data.get("venue", "paper")
        if venue in self.open_positions_by_venue:
            return venue
        order_id = data.get("order_id", "")
        for vname, vpos in self.open_positions_by_venue.items():
            if order_id in vpos:
                return vname
        state_key = data.get("_state_key", "")
        for vname, sk in VENUE_STATE_KEYS.items():
            if sk == state_key:
                return vname
        return "paper"

    async def update_from_trade_results(self, data: dict):
        try:
            status = str(data.get("status", ""))
            pnl = float(data.get("pnl_usd", 0))
            venue = self._resolve_venue(data)

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
                if venue in self.open_positions_by_venue and order_id in self.open_positions_by_venue[venue]:
                    del self.open_positions_by_venue[venue][order_id]

                symbol = data.get("symbol", "")
                if symbol in self.signal_cooldowns:
                    del self.signal_cooldowns[symbol]

                await self.save_portfolio_state()
                logger.debug(f"Closed position {order_id} ({venue}): pnl=${pnl:.2f}, count={len(self.open_positions)}")

            elif status == "open":
                order_id = data.get("order_id", "")
                symbol = data.get("symbol", "")
                side = data.get("side", "")
                quantity_usd = float(data.get("quantity_usd", 0))
                pos_data = {
                    "symbol": symbol,
                    "side": side,
                    "quantity_usd": quantity_usd,
                    "venue": venue,
                }
                self.open_positions[order_id] = pos_data
                if venue in self.open_positions_by_venue:
                    self.open_positions_by_venue[venue][order_id] = pos_data
                self.cash_available -= quantity_usd
                await self.save_portfolio_state()

                per_venue_counts = {v: len(self.open_positions_by_venue[v]) for v in VENUE_STATE_KEYS}
                logger.info(
                    f"Tracked new position {order_id} ({venue}): ${quantity_usd:.2f}, "
                    f"per_venue={per_venue_counts}, combined={len(self.open_positions)}"
                )

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

        auto_tune_task = asyncio.create_task(self.auto_tuner.loop())

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

        auto_tune_task.cancel()


class RiskAutoTuner:
    """Queries Ollama periodically to suggest optimal risk parameters based on live trading stats.
    Applies smoothed changes to avoid abrupt shifts. Works alongside evolution-agent (coarse, 6h)."""

    SYSTEM_PROMPT = (
        "You are a risk parameter optimizer for a crypto trading system. "
        "Based on live trading performance, suggest optimal min_confidence (0.35-0.70) "
        "and min_risk_reward (0.8-3.0). "
        "Rules: if win_rate < 30% or losing streak, tighten parameters. "
        "If win_rate > 55% and PnL positive, relax slightly. "
        "Respond ONLY with JSON: {\"min_confidence\": 0.XX, \"min_risk_reward\": X.X, \"reasoning\": \"...\"}"
    )

    def __init__(self, risk_manager: "RiskManager"):
        self.rm = risk_manager
        self.enabled = RISK_AUTOTUNE_ENABLED
        self.interval = RISK_AUTOTUNE_INTERVAL
        self.model = RISK_AUTOTUNE_MODEL
        self.ollama_host = OLLAMA_HOST
        self._conf_ema: float | None = None
        self._rr_ema: float | None = None
        self._alpha = 0.3

    async def _collect_stats(self) -> dict:
        rm = self.rm
        stats = {
            "current_params": {
                "min_confidence": rm.evo_params.get("min_confidence", 0.5),
                "min_risk_reward": rm.evo_params.get("min_risk_reward", 1.5),
            },
            "portfolio_value": round(rm.portfolio_value, 2),
            "peak_value": round(rm.peak_value, 2),
            "drawdown_pct": round((1 - rm.portfolio_value / max(rm.peak_value, 1)) * 100, 1),
            "total_trades": rm.total_trades,
            "winning_trades": rm.winning_trades,
            "win_rate": round(rm.winning_trades / max(rm.total_trades, 1) * 100, 1),
            "consecutive_losses": rm.consecutive_losses,
            "open_positions": len(rm.open_positions),
            "cash_available": round(rm.cash_available, 2),
        }
        try:
            rows = await rm.db.fetch(
                "SELECT symbol, side, pnl_usd, venue FROM trades "
                "WHERE status = 'closed' ORDER BY time DESC LIMIT 20"
            )
            stats["recent_trades"] = [
                {"symbol": r["symbol"], "side": r["side"], "pnl": round(float(r["pnl_usd"] or 0), 2), "venue": r.get("venue", "paper")}
                for r in rows
            ]
        except Exception:
            stats["recent_trades"] = []
        return stats

    def _build_prompt(self, stats: dict) -> str:
        lines = ["Current trading state:"]
        lines.append(f"  Portfolio: ${stats['portfolio_value']} (peak ${stats['peak_value']}, dd {stats['drawdown_pct']}%)")
        lines.append(f"  Trades: {stats['total_trades']} total, {stats['winning_trades']} wins, win_rate={stats['win_rate']}%")
        lines.append(f"  Consecutive losses: {stats['consecutive_losses']}")
        lines.append(f"  Open positions: {stats['open_positions']}, cash: ${stats['cash_available']}")
        lines.append(f"  Current min_confidence={stats['current_params']['min_confidence']}, min_risk_reward={stats['current_params']['min_risk_reward']}")
        if stats["recent_trades"]:
            lines.append("  Recent closed trades:")
            for t in stats["recent_trades"][:10]:
                lines.append(f"    {t['symbol']} {t['side']} pnl={t['pnl']} venue={t['venue']}")
        return "\n".join(lines)

    def _parse_response(self, text: str) -> Optional[dict]:
        import re
        for pattern in [r"\{[^{}]*\"min_confidence\"[^{}]*\}", r"\{[^{}]*\"min_risk_reward\"[^{}]*\}"]:
            m = re.search(pattern, text, re.DOTALL)
            if m:
                try:
                    parsed = json.loads(m.group())
                    if "min_confidence" in parsed and "min_risk_reward" in parsed:
                        return parsed
                except json.JSONDecodeError:
                    continue
        return None

    async def tune(self):
        if not self.enabled or not self.rm.running:
            return
        try:
            stats = await self._collect_stats()
            if stats["total_trades"] < 2:
                logger.info("RiskAutoTuner: not enough trades to tune (<2), skipping")
                return

            prompt = self._build_prompt(stats)
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self.ollama_host}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": prompt,
                        "system": self.SYSTEM_PROMPT,
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 150},
                    },
                )
                response.raise_for_status()
                data = response.json()
                raw = data.get("response", "")
                parsed = self._parse_response(raw)
                if not parsed:
                    logger.warning(f"RiskAutoTuner: could not parse Ollama response: {raw[:150]}")
                    return

            new_conf = float(parsed["min_confidence"])
            new_rr = float(parsed["min_risk_reward"])
            new_conf = max(0.35, min(0.70, new_conf))
            new_rr = max(0.8, min(3.0, new_rr))

            if self._conf_ema is None:
                self._conf_ema = new_conf
                self._rr_ema = new_rr
            else:
                self._conf_ema = self._alpha * new_conf + (1 - self._alpha) * self._conf_ema
                self._rr_ema = self._alpha * new_rr + (1 - self._alpha) * self._rr_ema

            old_conf = self.rm.evo_params.get("min_confidence", 0.5)
            old_rr = self.rm.evo_params.get("min_risk_reward", 1.5)
            self.rm.evo_params["min_confidence"] = round(self._conf_ema, 2)
            self.rm.evo_params["min_risk_reward"] = round(self._rr_ema, 2)

            logger.info(
                f"RiskAutoTuner: conf={old_conf:.2f}→{self._conf_ema:.2f} "
                f"rr={old_rr:.2f}→{self._rr_ema:.2f} | {parsed.get('reasoning', '')[:100]}"
            )

            await self.rm.redis.set_json("risk:auto_tune", {
                "min_confidence": round(self._conf_ema, 2),
                "min_risk_reward": round(self._rr_ema, 2),
                "reasoning": parsed.get("reasoning", ""),
                "stats_snapshot": {
                    "win_rate": stats["win_rate"],
                    "consecutive_losses": stats["consecutive_losses"],
                    "drawdown_pct": stats["drawdown_pct"],
                },
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.warning(f"RiskAutoTuner: tune failed: {e}")

    async def loop(self):
        while self.rm.running:
            try:
                await self.tune()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"RiskAutoTuner loop error: {e}")
            await asyncio.sleep(self.interval)


async def main():
    manager = RiskManager()
    await manager.run()


if __name__ == "__main__":
    asyncio.run(main())