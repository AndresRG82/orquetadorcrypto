import asyncio
import json
import logging
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.db import Database
from shared.attribution import run_full_attribution

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("backtesting")

try:
    import vectorbt as vbt
    VBT_AVAILABLE = True
    logger.info(f"vectorbt {vbt.__version__} loaded successfully")
except ImportError:
    VBT_AVAILABLE = False
    logger.warning("vectorbt not available, falling back to manual backtesting")


class BacktestEngine:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.db: Database | None = None
        self.running = False

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        logger.info("Backtesting Engine initialized (vectorbt mode)")

    async def fetch_ohlcv(self, symbol: str, timeframe: str, days: int = 30) -> Optional[pd.DataFrame]:
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
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.dropna(inplace=True)
        return df

    def compute_signals(self, df: pd.DataFrame, strategy: str, params: dict, df_htf: Optional[pd.DataFrame] = None) -> tuple[pd.Series, pd.Series]:
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        rsi = rsi.fillna(50)

        ema_9 = close.ewm(span=9, adjust=False).mean()
        ema_21 = close.ewm(span=21, adjust=False).mean()
        ema_50 = close.ewm(span=50, adjust=False).mean()

        ema_12 = close.ewm(span=12, adjust=False).mean()
        ema_26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema_12 - ema_26
        macd_signal = macd_line.ewm(span=9, adjust=False).mean()

        bb_mid = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std

        atr = (high - low).rolling(14).mean()

        bb_range = bb_upper - bb_lower
        bb_pos = (close - bb_lower) / bb_range.replace(0, np.nan)

        rsi_os = params.get("rsi_oversold", 30)
        rsi_ob = params.get("rsi_overbought", 70)
        macd_hist = macd_line - macd_signal
        min_score = params.get("min_score", 3)

        vol_ma = volume.rolling(20).mean()
        vol_ok = volume > vol_ma * 0.8

        htf_bullish = pd.Series(True, index=df.index)
        htf_bearish = pd.Series(True, index=df.index)
        if df_htf is not None and len(df_htf) > 21:
            htf_close = df_htf["close"]
            htf_close = htf_close[~htf_close.index.duplicated(keep="last")]
            htf_ema_9 = htf_close.ewm(span=9, adjust=False).mean()
            htf_ema_21 = htf_close.ewm(span=21, adjust=False).mean()
            htf_ema_50 = htf_close.ewm(span=50, adjust=False).mean()
            htf_trend_up = (htf_ema_9 > htf_ema_21) & (htf_ema_21 > htf_ema_50)
            htf_trend_down = (htf_ema_9 < htf_ema_21) & (htf_ema_21 < htf_ema_50)
            htf_trend_up_reindexed = htf_trend_up.reindex(df.index, method="ffill").fillna(False)
            htf_trend_down_reindexed = htf_trend_down.reindex(df.index, method="ffill").fillna(False)
            htf_bullish = htf_trend_up_reindexed | ~htf_trend_down_reindexed
            htf_bearish = htf_trend_down_reindexed | ~htf_trend_up_reindexed

        if strategy == "scalping":
            rsi_os_s = params.get("rsi_oversold_strong", 25)
            rsi_os_w = params.get("rsi_oversold_weak", 35)
            rsi_ob_s = params.get("rsi_overbought_strong", 75)
            rsi_ob_w = params.get("rsi_overbought_weak", 65)
            bb_p_low = params.get("bb_position_low", 0.15)
            bb_p_high = params.get("bb_position_high", 0.85)

            score = pd.Series(0.0, index=close.index)
            score = score + np.where(rsi < rsi_os_s, 2, np.where(rsi < rsi_os_w, 1, 0))
            score = score - np.where(rsi > rsi_ob_s, 2, np.where(rsi > rsi_ob_w, 1, 0))
            score = score + np.where((macd_line > macd_signal) & (macd_hist > 0), 1, 0)
            score = score - np.where((macd_line < macd_signal) & (macd_hist < 0), 1, 0)
            score = score + np.where(bb_pos < bb_p_low, 1, 0)
            score = score - np.where(bb_pos > bb_p_high, 1, 0)
            score = score + np.where(ema_9 > ema_21, 1, -1)

            buy_signal = (score >= min_score) & vol_ok & htf_bullish
            sell_signal = (score <= -min_score) & vol_ok & htf_bearish
        elif strategy == "swing":
            buy_signal = (
                ((rsi < rsi_os) |
                ((ema_9 > ema_21) & (ema_21 > ema_50)) |
                ((macd_line > macd_signal) & (rsi < 45)))
                & vol_ok & htf_bullish
            )
            sell_signal = (
                ((rsi > rsi_ob) |
                ((ema_9 < ema_21) & (ema_21 < ema_50)) |
                ((macd_line < macd_signal) & (rsi > 55)))
                & vol_ok & htf_bearish
            )
        elif strategy == "arbitrage":
            bb_width = bb_range / bb_mid.replace(0, np.nan)
            buy_signal = (bb_width < 0.03) & (bb_pos < 0.05)
            sell_signal = (bb_width < 0.03) & (bb_pos > 0.95)
        else:
            buy_signal = pd.Series(False, index=df.index)
            sell_signal = pd.Series(False, index=df.index)

        return buy_signal.fillna(False), sell_signal.fillna(False)

    def run_vectorbt_backtest(self, df: pd.DataFrame, strategy: str, params: dict, initial_capital: float = 1000.0, df_htf: Optional[pd.DataFrame] = None) -> tuple[dict, Optional[pd.Series]]:
        buy_signal, sell_signal = self.compute_signals(df, strategy, params, df_htf=df_htf)

        if buy_signal.sum() == 0 and sell_signal.sum() == 0:
            return {"strategy": strategy, "total_trades": 0, "message": "No signals generated"}, None

        atr_sl = params.get("atr_sl_multiplier", 1.5)
        atr_tp = params.get("atr_tp_multiplier", 3.0)
        atr = (df["high"] - df["low"]).rolling(14).mean()

        entries = buy_signal
        exits = sell_signal

        portfolio = vbt.Portfolio.from_signals(
            df["close"], entries, exits,
            init_cash=initial_capital,
            fees=0.00075,
            slippage=0.001,
            freq="1min" if len(df) > 1000 else "1h",
        )

        import math
        stats = portfolio.stats()
        total_pnl = float(portfolio.total_return()) * initial_capital
        total_return_pct = float(portfolio.total_return()) * 100

        def _safe_float(val, default=0.0):
            v = float(val) if pd.notna(val) else default
            return default if math.isinf(v) or math.isnan(v) else v

        sharpe = _safe_float(stats.get("Sharpe Ratio", 0))
        max_dd = _safe_float(stats.get("Max Drawdown [%]", 0))
        total_trades = int(_safe_float(stats.get("Total Trades", 0)))
        win_rate = _safe_float(stats.get("Win Rate [%]", 0))
        profit_factor = _safe_float(stats.get("Profit Factor", 0))

        return {
            "strategy": strategy,
            "final_equity": float(portfolio.value().iloc[-1]) if len(portfolio.value()) > 0 else initial_capital,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_return_pct,
            "total_trades": total_trades,
            "winning_trades": int(total_trades * win_rate / 100) if total_trades > 0 else 0,
            "losing_trades": int(total_trades * (100 - win_rate) / 100) if total_trades > 0 else 0,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_dd,
            "sharpe_ratio": sharpe,
        }, portfolio.returns()

    def run_vectorbt_sweep(self, df: pd.DataFrame, strategy: str, param_grid: dict, initial_capital: float = 1000.0) -> list[dict]:
        if not VBT_AVAILABLE:
            return [{"error": "vectorbt not available"}]

        keys = list(param_grid.keys())
        values = list(param_grid.values())

        from itertools import product
        all_combos = list(product(*values))
        logger.info(f"Running vectorbt sweep: {len(all_combos)} parameter combinations for {strategy}")

        results = []
        for combo in all_combos:
            params = dict(zip(keys, combo))
            try:
                result, _ = self.run_vectorbt_backtest(df, strategy, params, initial_capital)
                result["params"] = params
                results.append(result)
            except Exception as e:
                results.append({"params": params, "error": str(e)})

        results = [r for r in results if "error" not in r]
        results.sort(key=lambda x: x.get("sharpe_ratio", 0), reverse=True)

        logger.info(f"Sweep complete: {len(results)} valid results, best Sharpe={results[0]['sharpe_ratio']:.2f}" if results else "No valid results")
        return results

    def _split_train_test(self, df: pd.DataFrame, test_split: float = 0.2) -> tuple[pd.DataFrame, pd.DataFrame]:
        split_idx = int(len(df) * (1 - test_split))
        df_train = df.iloc[:split_idx].copy()
        df_test = df.iloc[split_idx:].copy()
        return df_train, df_test

    def run_walk_forward(self, df: pd.DataFrame, strategy: str, params: dict,
                         df_htf: Optional[pd.DataFrame] = None,
                         window_size: int = 2000, test_size: int = 500,
                         step_size: int = 500,
                         initial_capital: float = 1000.0) -> dict:
        total_len = len(df)
        min_required = window_size + test_size
        if total_len < min_required:
            return {"type": "walk_forward", "error": f"Insufficient data: {total_len} bars, need {min_required}",
                    "total_walks": 0}

        start_positions = list(range(0, total_len - min_required + 1, step_size))
        logger.info(f"Walk-forward: {len(start_positions)} windows over {total_len} bars "
                    f"(window={window_size}, test={test_size}, step={step_size})")

        walks = []
        for i, start in enumerate(start_positions):
            train_end = start + window_size
            test_end = train_end + test_size
            df_train = df.iloc[start:train_end].copy()
            df_test = df.iloc[train_end:test_end].copy()

            df_htf_train = None
            df_htf_test = None
            if df_htf is not None and len(df_htf) > 50:
                htf_total = len(df_htf)
                htf_start = int(start * htf_total / total_len)
                htf_mid = int(train_end * htf_total / total_len)
                htf_end = int(test_end * htf_total / total_len)
                df_htf_train = df_htf.iloc[htf_start:htf_mid].copy() if htf_mid > htf_start else None
                df_htf_test = df_htf.iloc[htf_mid:htf_end].copy() if htf_end > htf_mid else None

            train_result, _ = self.run_vectorbt_backtest(df_train, strategy, params, initial_capital, df_htf=df_htf_train)
            test_result, _ = self.run_vectorbt_backtest(df_test, strategy, params, initial_capital, df_htf=df_htf_test)

            walk = {
                "window": i,
                "train_bars": len(df_train),
                "test_bars": len(df_test),
                "train": {k: train_result.get(k) for k in ("sharpe_ratio", "total_pnl", "win_rate", "total_trades")},
                "test": {k: test_result.get(k) for k in ("sharpe_ratio", "total_pnl", "win_rate", "total_trades")},
            }
            walks.append(walk)
            logger.info(
                f"  Walk[{i}] train: Sharpe={train_result.get('sharpe_ratio', 0):.2f} "
                f"PnL=${train_result.get('total_pnl', 0):.2f} | "
                f"test: Sharpe={test_result.get('sharpe_ratio', 0):.2f} "
                f"PnL=${test_result.get('total_pnl', 0):.2f}"
            )

        if not walks:
            return {"type": "walk_forward", "error": "No walks completed", "total_walks": 0}

        def _safe_extract(key, default=0.0):
            vals = []
            for w in walks:
                v = w["test"].get(key)
                if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
                    v = default
                vals.append(v)
            return np.array(vals, dtype=np.float64)

        oos_sharpes = _safe_extract("sharpe_ratio")
        oos_pnls = _safe_extract("total_pnl")
        positive_sharpe_walks = int(np.sum(oos_sharpes > 0))

        def _best_walk(key, reverse=True):
            valid = [w for w in walks if isinstance(w["test"].get(key), (int, float))
                     and not np.isnan(w["test"][key]) and not np.isinf(w["test"][key])]
            if not valid:
                return None
            return max(valid, key=lambda w: w["test"].get(key, -1e9) if reverse else -w["test"].get(key, 1e9))

        return {
            "type": "walk_forward",
            "total_walks": len(walks),
            "window_size": window_size,
            "test_size": test_size,
            "step_size": step_size,
            "avg_oos_sharpe": float(np.mean(oos_sharpes)) if len(oos_sharpes) > 0 else 0.0,
            "avg_oos_pnl": float(np.mean(oos_pnls)) if len(oos_pnls) > 0 else 0.0,
            "consistency_pct": float(positive_sharpe_walks / len(walks) * 100) if walks else 0.0,
            "oos_sharpe_std": float(np.std(oos_sharpes)) if len(oos_sharpes) > 1 else 0.0,
            "oos_sharpe_ratio": float(np.mean(oos_sharpes)) if len(oos_sharpes) > 0 else 0.0,
            "best_walk": _best_walk("sharpe_ratio", reverse=True),
            "worst_walk": _best_walk("sharpe_ratio", reverse=False),
            "walk_count_with_positive_sharpe": positive_sharpe_walks,
        }

    async def run_backtest(self, strategy: str, symbol: str, timeframe: str, days: int = 30,
                           initial_capital: float = 1000.0, params: dict = None,
                           test_split: float = 0.0) -> dict:
        df = await self.fetch_ohlcv(symbol, timeframe, days)
        if df is None or len(df) < 50:
            return {"error": f"Insufficient data for {symbol} {timeframe}", "strategy": strategy}

        if params is None:
            params = await self.redis.get_json(f"strategy:params:{strategy}") or {}

        htf_map = {"scalping": "1h", "swing": "1d"}
        df_htf = None
        if strategy in htf_map:
            df_htf = await self.fetch_ohlcv(symbol, htf_map[strategy], days)

        result, portfolio_returns = self.run_vectorbt_backtest(df, strategy, params, initial_capital, df_htf=df_htf)
        result["symbol"] = symbol
        result["timeframe"] = timeframe
        result["period_days"] = days

        if test_split > 0 and len(df) > 100:
            df_train, df_test = self._split_train_test(df, test_split)

            df_htf_train = df_htf.iloc[:int(len(df_htf) * (1 - test_split))].copy() if df_htf is not None and len(df_htf) > 50 else None
            df_htf_test = df_htf.iloc[int(len(df_htf) * (1 - test_split)):].copy() if df_htf is not None and len(df_htf) > 50 else None

            train_result, _ = self.run_vectorbt_backtest(df_train, strategy, params, initial_capital, df_htf=df_htf_train)
            test_result, _ = self.run_vectorbt_backtest(df_test, strategy, params, initial_capital, df_htf=df_htf_test)

            result["in_sample"] = {k: train_result.get(k) for k in ("sharpe_ratio", "total_pnl", "win_rate", "max_drawdown_pct", "profit_factor", "total_trades")}
            result["out_of_sample"] = {k: test_result.get(k) for k in ("sharpe_ratio", "total_pnl", "win_rate", "max_drawdown_pct", "profit_factor", "total_trades")}
            result["oos_sharpe_ratio"] = test_result.get("sharpe_ratio", 0)
            result["test_split"] = test_split
            logger.info(
                f"  In-sample:  Sharpe={train_result.get('sharpe_ratio', 0):.2f} "
                f"PnL=${train_result.get('total_pnl', 0):.2f}"
            )
            logger.info(
                f"  Out-of-sample: Sharpe={test_result.get('sharpe_ratio', 0):.2f} "
                f"PnL=${test_result.get('total_pnl', 0):.2f}"
            )
        else:
            result["test_split"] = 0.0

        if portfolio_returns is not None and len(portfolio_returns) > 10:
            benchmark_returns = df["close"].pct_change().dropna()
            try:
                attribution = run_full_attribution(portfolio_returns, benchmark_returns)
                result["attribution"] = {k: v for k, v in attribution.items() if v}
            except Exception as e:
                logger.warning(f"Attribution failed: {e}")

        await self.redis.set_json(f"backtest:{strategy}:{symbol}:{timeframe}", result)
        logger.info(
            f"Backtest {strategy} {symbol} {timeframe}: PnL=${result.get('total_pnl', 0):.2f} "
            f"WR={result.get('win_rate', 0):.1f}% PF={result.get('profit_factor', 0):.2f} "
            f"Sharpe={result.get('sharpe_ratio', 0):.2f} MaxDD={result.get('max_drawdown_pct', 0):.1f}%"
        )
        return result

    async def run_sweep(self, strategy: str, symbol: str, timeframe: str, days: int = 30,
                        param_grid: dict = None) -> list[dict]:
        df = await self.fetch_ohlcv(symbol, timeframe, days)
        if df is None or len(df) < 50:
            return [{"error": f"Insufficient data for {symbol} {timeframe}"}]

        if param_grid is None:
            param_grid = self._default_param_grid(strategy)

        results = self.run_vectorbt_sweep(df, strategy, param_grid)
        for r in results[:5]:
            key = f"sweep:{strategy}:{symbol}:{timeframe}:{hash(json.dumps(r.get('params', {}), sort_keys=True)) % 10000}"
            await self.redis.set_json(key, r, ex=86400)
        await self.redis.set_json(f"sweep:latest:{strategy}", {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol, "timeframe": timeframe,
            "total_combos": len(results),
            "top_5": results[:5],
        })
        return results

    def _default_param_grid(self, strategy: str) -> dict:
        if strategy == "scalping":
            return {
                "rsi_oversold": [20, 25, 30, 35],
                "rsi_overbought": [65, 70, 75, 80],
                "bb_position_low": [0.10, 0.15, 0.20],
                "bb_position_high": [0.80, 0.85, 0.90],
                "atr_sl_multiplier": [1.0, 1.5, 2.0],
                "atr_tp_multiplier": [1.5, 2.0, 2.5, 3.0],
            }
        elif strategy == "swing":
            return {
                "rsi_oversold": [25, 30, 35],
                "rsi_overbought": [65, 70, 75],
                "atr_sl_multiplier": [1.0, 1.5, 2.0],
                "atr_tp_multiplier": [2.0, 3.0, 4.0, 5.0],
            }
        elif strategy == "arbitrage":
            return {
                "bb_position_low": [0.03, 0.05, 0.08],
                "bb_position_high": [0.92, 0.95, 0.97],
                "atr_sl_multiplier": [0.8, 1.0, 1.5],
                "atr_tp_multiplier": [1.5, 2.0, 2.5],
            }
        return {}

    WALK_FORWARD_CONFIG = {
        "scalping": {"5m": {"window_size": 2000, "test_size": 500, "step_size": 500}},
    }

    async def run_all_backtests(self):
        strategies = ["scalping", "swing", "arbitrage"]
        symbols = settings.TOP_PAIRS[:5]
        timeframes_map = {"scalping": ["5m", "15m"], "swing": ["1h", "4h"], "arbitrage": ["1h", "4h"]}

        results = []
        for strategy in strategies:
            for symbol in symbols:
                for tf in timeframes_map.get(strategy, ["1h"]):
                    wf_config = self.WALK_FORWARD_CONFIG.get(strategy, {}).get(tf)
                    if wf_config:
                        result = await self.run_walk_forward_backtest(
                            strategy, symbol, tf, days=30, **wf_config
                        )
                    else:
                        result = await self.run_backtest(strategy, symbol, tf, days=30, test_split=0.2)
                    results.append(result)

        await self.redis.set_json("backtest:latest", {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results": results,
        })
        return results

    async def run_walk_forward_backtest(self, strategy: str, symbol: str, timeframe: str,
                                         days: int = 30, initial_capital: float = 1000.0,
                                         params: dict = None,
                                         window_size: int = 2000, test_size: int = 500,
                                         step_size: int = 500) -> dict:
        df = await self.fetch_ohlcv(symbol, timeframe, days)
        if df is None or len(df) < 50:
            return {"error": f"Insufficient data for {symbol} {timeframe}", "strategy": strategy}

        if params is None:
            params = await self.redis.get_json(f"strategy:params:{strategy}") or {}

        htf_map = {"scalping": "1h", "swing": "1d"}
        df_htf = None
        if strategy in htf_map:
            df_htf = await self.fetch_ohlcv(symbol, htf_map[strategy], days)

        wf_result = self.run_walk_forward(df, strategy, params, df_htf=df_htf,
                                           window_size=window_size, test_size=test_size,
                                           step_size=step_size, initial_capital=initial_capital)

        result = {"strategy": strategy, "symbol": symbol, "timeframe": timeframe,
                   "period_days": days, "walk_forward": wf_result}
        if wf_result.get("total_walks", 0) > 0:
            result["oos_sharpe_ratio"] = wf_result["avg_oos_sharpe"]
            result["consistency_pct"] = wf_result["consistency_pct"]

        await self.redis.set_json(f"backtest:{strategy}:{symbol}:{timeframe}", result)
        logger.info(
            f"Walk-forward {strategy} {symbol} {timeframe}: {wf_result.get('total_walks', 0)} walks, "
            f"avg OOS Sharpe={wf_result.get('avg_oos_sharpe', 0):.2f} "
            f"consistency={wf_result.get('consistency_pct', 0):.1f}%"
        )
        return result

    async def run(self):
        self.running = True
        await self.initialize()
        logger.info("Backtesting Engine running (vectorbt mode, every 6h)")
        while self.running:
            try:
                await self.redis.heartbeat("backtesting")
                await self.run_all_backtests()
                await asyncio.sleep(21600)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Backtest error: {e}")
                await asyncio.sleep(300)


async def main():
    engine = BacktestEngine()
    await engine.run()


if __name__ == "__main__":
    asyncio.run(main())
