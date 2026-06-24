import asyncio
import logging
import math
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import pandas as pd

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("nautilus-bridge")

NAUTILUS_AVAILABLE = False
try:
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.trading.strategy import Strategy
    from nautilus_trader.indicators.rsi import RelativeStrengthIndex
    from nautilus_trader.indicators.ema import ExponentialMovingAverage
    from nautilus_trader.indicators.bollinger_bands import BollingerBands
    from nautilus_trader.indicators.atr import AverageTrueRange
    from nautilus_trader.model.data import Bar, BarType, BarSpecification
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.currencies import USDT
    from nautilus_trader.model.enums import OrderSide
    NAUTILUS_AVAILABLE = True
    logger.info("nautilus_trader loaded successfully")
except ImportError as e:
    Strategy = object
    logger.warning("nautilus_trader not available: %s", e)


def safe_float(val, default=0.0):
    v = float(val) if val is not None else default
    return default if math.isinf(v) or math.isnan(v) else v


def bar_spec(timeframe: str) -> "BarSpecification":
    t = {"1m": (1, BarSpecification.MINUTE), "5m": (5, BarSpecification.MINUTE),
         "15m": (15, BarSpecification.MINUTE), "30m": (30, BarSpecification.MINUTE),
         "1h": (1, BarSpecification.HOUR), "4h": (4, BarSpecification.HOUR),
         "1d": (1, BarSpecification.DAY)}.get(timeframe, (1, BarSpecification.HOUR))
    return BarSpecification(t[0], t[1])


def make_instrument(symbol: str) -> "CryptoPerpetual":
    base = symbol.split("/")[0]
    sid = Symbol(symbol.replace("/", ""))
    return CryptoPerpetual(
        instrument_id=InstrumentId(sid, Venue("BINANCE")),
        raw_symbol=sid, base_currency=base, quote_currency=USDT,
        settlement_currency=USDT, price_precision=2, size_precision=6,
        price_increment=Decimal("0.01"), size_increment=Decimal("0.000001"),
        max_quantity=Decimal("1000"), min_quantity=Decimal("0.0001"),
        maker_fee=Decimal("0.00075"), taker_fee=Decimal("0.00075"),
    )


def df_to_bars(df: pd.DataFrame, symbol: str, timeframe: str) -> list:
    if df is None or df.empty:
        return []
    inst_id = InstrumentId(Symbol(symbol.replace("/", "")), Venue("BINANCE"))
    bt = BarType(inst_id, bar_spec(timeframe))
    bars = []
    for idx, row in df.iterrows():
        bars.append(Bar(
            bar_type=bt,
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=Decimal(str(row.get("volume", 0))),
            ts_event=int(idx.timestamp() * 1_000_000_000),
            ts_init=int(idx.timestamp() * 1_000_000_000),
        ))
    return bars


class _NautilusScalpingStrategy(Strategy):
    def __init__(self, config: dict, instrument_id: InstrumentId, bar_type):
        super().__init__(config)
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.p = config

    def on_start(self):
        self.rsi = RelativeStrengthIndex(14)
        self.ema_f = ExponentialMovingAverage(9)
        self.ema_s = ExponentialMovingAverage(21)
        self.bb = BollingerBands(20, 2.0)
        for ind in [self.rsi, self.ema_f, self.ema_s, self.bb]:
            self.register_indicator(ind)

    def on_bar(self, bar: Bar):
        if not self.rsi.initialized or not self.bb.initialized:
            return
        r = self.rsi.value
        bp = (float(bar.close) - self.bb.lower) / (self.bb.upper - self.bb.lower + 1e-10)
        score = 0
        if r < self.p.get("rsi_oversold_strong", 25): score += 2
        elif r < self.p.get("rsi_oversold_weak", 35): score += 1
        elif r > self.p.get("rsi_overbought_strong", 75): score -= 2
        elif r > self.p.get("rsi_overbought_weak", 65): score -= 1
        if bp < self.p.get("bb_position_low", 0.15): score += 2
        elif bp > self.p.get("bb_position_high", 0.85): score -= 2
        if self.ema_f.value > self.ema_s.value: score += 1
        else: score -= 1
        ms = self.p.get("min_score", 3)
        if score >= ms and self.portfolio.is_flat(self.instrument_id):
            size = Decimal(str(float(self.portfolio.equity(self.instrument_id)) * 0.2))
            self.order_factory.market(self.instrument_id, OrderSide.BUY, size)
        elif score <= -ms and self.portfolio.is_flat(self.instrument_id):
            size = Decimal(str(float(self.portfolio.equity(self.instrument_id)) * 0.2))
            self.order_factory.market(self.instrument_id, OrderSide.SELL, size)


class _NautilusSwingStrategy(Strategy):
    def __init__(self, config: dict, instrument_id: InstrumentId, bar_type):
        super().__init__(config)
        self.instrument_id = instrument_id
        self.bar_type = bar_type
        self.p = config

    def on_start(self):
        self.rsi = RelativeStrengthIndex(14)
        self.e9 = ExponentialMovingAverage(9)
        self.e21 = ExponentialMovingAverage(21)
        self.e50 = ExponentialMovingAverage(50)
        for ind in [self.rsi, self.e9, self.e21, self.e50]:
            self.register_indicator(ind)

    def on_bar(self, bar: Bar):
        if not self.rsi.initialized or not self.e50.initialized:
            return
        r = self.rsi.value
        a, b, c = float(self.e9.value), float(self.e21.value), float(self.e50.value)
        if a > b > c and r < 65 and self.portfolio.is_flat(self.instrument_id):
            size = Decimal(str(float(self.portfolio.equity(self.instrument_id)) * 0.25))
            self.order_factory.market(self.instrument_id, OrderSide.BUY, size)
        elif a < b < c and r > 35 and self.portfolio.is_flat(self.instrument_id):
            size = Decimal(str(float(self.portfolio.equity(self.instrument_id)) * 0.25))
            self.order_factory.market(self.instrument_id, OrderSide.SELL, size)


def run_backtest(df: pd.DataFrame, symbol: str, timeframe: str,
                 strategy_class, strategy_name: str, params: dict,
                 initial_capital: float = 1000.0) -> dict:
    if not NAUTILUS_AVAILABLE:
        return {"engine": "nautilus", "error": "nautilus_trader not available"}
    try:
        engine = BacktestEngine()
        engine.add_venue(Venue("BINANCE"), oms_type="NETTING",
                         account_type="CASH", base_currency=USDT,
                         starting_equity=Decimal(str(initial_capital)))
        inst = make_instrument(symbol)
        engine.add_instrument(inst)
        bars = df_to_bars(df, symbol, timeframe)
        engine.add_data(bars)
        strat = strategy_class({**params, "instrument_id": inst.id,
                                "bar_type": BarType(inst.id, bar_spec(timeframe))},
                               inst.id, BarType(inst.id, bar_spec(timeframe)))
        engine.add_strategy(strat)
        engine.run()
        result = engine.get_result()
        stats = {
            "total_trades": safe_float(getattr(result, "total_trades", 0), 0),
            "win_rate": safe_float(getattr(result, "win_rate", 0), 0),
            "total_pnl": safe_float(getattr(result, "total_pnl", 0), 0),
            "total_return_pct": safe_float(getattr(result, "total_return_pct", 0), 0),
            "sharpe_ratio": safe_float(getattr(result, "sharpe", 0), 0),
            "max_drawdown_pct": safe_float(getattr(result, "max_drawdown", 0), 0),
            "final_equity": safe_float(getattr(result, "final_equity", 0), 0),
        }
        return {"engine": "nautilus", "strategy": strategy_name,
                "symbol": symbol, "timeframe": timeframe, **stats}
    except Exception as e:
        logger.error("Nautilus %s %s: %s", strategy_name, symbol, e)
        return {"engine": "nautilus", "error": str(e)}


class NautilusBridge:
    def __init__(self):
        self.redis: Optional[RedisClient] = None
        self.db: Optional[Database] = None
        self.running = False

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()

    async def fetch_ohlcv(self, symbol: str, timeframe: str, days: int = 60) -> Optional[pd.DataFrame]:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        rows = await self.db.fetch(
            "SELECT time, open, high, low, close, volume FROM ohlcv "
            "WHERE symbol = $1 AND timeframe = $2 AND time > $3 ORDER BY time ASC",
            symbol, timeframe, since,
        )
        if len(rows) < 50:
            return None
        df = pd.DataFrame([dict(r) for r in rows])
        df["time"] = pd.to_datetime(df["time"])
        df.set_index("time", inplace=True)
        for c in ["open", "high", "low", "close", "volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df.dropna(inplace=True)
        return df

    async def get_vbt_result(self, symbol: str, strategy: str, tf: str) -> dict:
        return await self.redis.get_json(f"backtest:{strategy}:{symbol}:{tf}") or {}

    async def run_pipeline(self):
        if not NAUTILUS_AVAILABLE:
            logger.info("nautilus_trader not available — skipping pipeline")
            return

        configs = [
            ("scalping", _NautilusScalpingStrategy, ["5m", "15m"]),
            ("swing", _NautilusSwingStrategy, ["1h", "4h"]),
        ]
        symbols = settings.TOP_PAIRS[:5]

        for sname, sclass, tfs in configs:
            for symbol in symbols:
                for tf in tfs:
                    df = await self.fetch_ohlcv(symbol, tf)
                    if df is None:
                        continue
                    params = await self.redis.get_json(f"strategy:params:{sname}") or {}
                    nr = run_backtest(df, symbol, tf, sclass, sname, params)
                    if "error" in nr:
                        logger.warning("Nautilus %s %s %s: %s", sname, symbol, tf, nr["error"])
                        continue
                    vbt = await self.get_vbt_result(symbol, sname, tf)
                    comp = {
                        "sharpe_nautilus": nr.get("sharpe_ratio", 0),
                        "sharpe_vectorbt": vbt.get("sharpe_ratio", 0),
                        "sharpe_diff": nr.get("sharpe_ratio", 0) - vbt.get("sharpe_ratio", 0),
                        "pnl_nautilus": nr.get("total_pnl", 0),
                        "pnl_vectorbt": vbt.get("total_pnl", 0),
                        "trades_nautilus": nr.get("total_trades", 0),
                        "trades_vectorbt": vbt.get("total_trades", 0),
                    }
                    payload = {"timestamp": datetime.now(timezone.utc).isoformat(),
                               "nautilus": nr, "comparison": comp}
                    await self.redis.set_json(f"nautilus:{sname}:{symbol}:{tf}", payload)
                    logger.info("Nautilus %s %s %s: PnL=%.2f Sharpe=%.2f (vbt=%.2f)",
                                sname, symbol, tf, nr.get("total_pnl", 0),
                                nr.get("sharpe_ratio", 0), vbt.get("sharpe_ratio", 0))

        await self.redis.set_json("nautilus:latest",
                                  {"timestamp": datetime.now(timezone.utc).isoformat(),
                                   "status": "completed"})

    async def run(self):
        self.running = True
        await self.initialize()
        logger.info("NautilusBridge running (every 6h)")
        while self.running:
            try:
                await self.run_pipeline()
                await asyncio.sleep(21600)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Bridge error: %s", e)
                await asyncio.sleep(300)


async def main():
    bridge = NautilusBridge()
    await bridge.run()


if __name__ == "__main__":
    asyncio.run(main())
