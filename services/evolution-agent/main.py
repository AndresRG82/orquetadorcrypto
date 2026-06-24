import asyncio
import json
import logging
import sys
import os
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional
from itertools import product

import httpx
import numpy as np
import pandas as pd

sys.path.insert(0, "/app")
from shared.config import settings
from shared.redis_client import RedisClient
from shared.db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("evolution-agent")

CYCLE_INTERVAL = 21600
ROLLBACK_PNL_THRESHOLD = -0.05
ROLLBACK_WINRATE_THRESHOLD = -0.10
MAX_CHANGES_PER_CYCLE = 5
SNAPSHOT_PREFIX = "evolution:snapshot:"
CURRENT_PREFIX = "evolution:current"
HISTORY_KEY = "evolution:history"

SYSTEM_PROMPT = """You are an expert algorithmic trading system optimizer. You receive a snapshot of the current system performance and parameters, and propose changes to improve profitability.

RULES:
- Respond ONLY with valid JSON, no other text, no markdown, no code blocks.
- Use this exact schema:
{
  "changes": [
    {
      "type": "tuning" | "strategy_config" | "risk" | "new_strategy",
      "target": "scalping" | "swing" | "arbitrage" | "risk_manager" | "new",
      "params": { ... },
      "reasoning": "brief explanation"
    }
  ],
  "analysis": "brief overall assessment"
}
- type "tuning": adjust technical indicator thresholds (rsi_overbought, rsi_oversold, macd_threshold, bb_width_threshold, bb_position_threshold, ema_diff_threshold, atr_sl_multiplier, atr_tp_multiplier)
- type "strategy_config": adjust strategy behavior (active, cooldown_seconds, confidence_weight, max_positions_per_strategy)
- type "risk": adjust risk parameters (max_position_pct, max_drawdown_pct, kelly_fraction, min_confidence, min_risk_reward, stop_loss_atr_mult, take_profit_atr_mult)
- type "new_strategy": create a new strategy (name, description, code, timeframes, params)
- Maximum 5 changes per cycle. Prefer fewer, high-confidence changes.
- Only propose changes if there is clear evidence of underperformance.
- For new strategies, include complete Python code in params.code using the same pattern as existing strategies.
- Do NOT propose changes that would increase max_position_pct above 0.30 or max_drawdown_pct above 0.20."""


class EvolutionAgent:
    def __init__(self):
        self.redis: RedisClient | None = None
        self.db: Database | None = None
        self.ollama_host = settings.OLLAMA_HOST
        self.model = settings.OLLAMA_MODEL
        self.running = False
        self.last_snapshot_time: Optional[datetime] = None

    async def initialize(self):
        self.redis = await RedisClient.get_instance()
        self.db = await Database.get_instance()
        logger.info(f"Evolution Agent initialized, model: {self.model}")

    async def collect_system_snapshot(self) -> dict:
        snapshot = {"timestamp": datetime.now(timezone.utc).isoformat()}

        stats = await self.redis.get_json("portfolio:stats")
        snapshot["portfolio"] = stats or {}

        sentiment = await self.redis.get_json("sentiment:current")
        snapshot["sentiment"] = sentiment or {}

        backtest = await self.redis.get_json("backtest:latest")
        snapshot["backtest"] = backtest or {}

        training = await self.redis.get_json("training:export_stats")
        snapshot["training"] = training or {}

        strategy_metrics = []
        try:
            rows = await self.db.fetch(
                "SELECT strategy, COUNT(*) as total, "
                "SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins, "
                "SUM(pnl_usd) as total_pnl, AVG(pnl_usd) as avg_pnl, "
                "AVG(CASE WHEN pnl_usd > 0 THEN pnl_usd END) as avg_win, "
                "AVG(CASE WHEN pnl_usd <= 0 THEN ABS(pnl_usd) END) as avg_loss "
                "FROM trades WHERE status='closed' AND time > NOW() - INTERVAL '7 days' GROUP BY strategy"
            )
            for r in rows:
                total = int(r["total"]) if r["total"] else 0
                wins = int(r["wins"]) if r["wins"] else 0
                strategy_metrics.append({
                    "strategy": r["strategy"] or "unknown",
                    "total_trades": total,
                    "win_rate": (wins / total * 100) if total > 0 else 0,
                    "total_pnl": float(r["total_pnl"]) if r["total_pnl"] else 0,
                    "avg_pnl": float(r["avg_pnl"]) if r["avg_pnl"] else 0,
                    "avg_win": float(r["avg_win"]) if r["avg_win"] else 0,
                    "avg_loss": float(r["avg_loss"]) if r["avg_loss"] else 0,
                })
        except Exception as e:
            logger.warning(f"Could not fetch strategy metrics: {e}")
        snapshot["strategy_metrics"] = strategy_metrics

        current_params = {}
        for strategy in ["scalping", "swing", "arbitrage"]:
            p = await self.redis.get_json(f"strategy:params:{strategy}")
            if p:
                current_params[strategy] = p
        risk_p = await self.redis.get_json("risk:params")
        if risk_p:
            current_params["risk_manager"] = risk_p
        for strategy in ["scalping", "swing", "arbitrage"]:
            c = await self.redis.get_json(f"strategy:config:{strategy}")
            if c:
                current_params.setdefault(strategy, {}).update(c)
        snapshot["current_params"] = current_params

        recent_feedback = []
        try:
            rows = await self.db.fetch(
                "SELECT insights, analysis_correct, pnl FROM qwen_feedback ORDER BY time DESC LIMIT 10"
            )
            recent_feedback = [dict(r) for r in rows]
        except Exception:
            pass
        snapshot["recent_feedback"] = recent_feedback

        return snapshot

    async def fetch_ohlcv_for_sweep(self, symbol: str, timeframe: str, days: int = 30) -> Optional[pd.DataFrame]:
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
            buy_signal = (bb_width < 0.03) & (bb_pos < params.get("bb_position_low", 0.05))
            sell_signal = (bb_width < 0.03) & (bb_pos > params.get("bb_position_high", 0.95))
        else:
            buy_signal = pd.Series(False, index=df.index)
            sell_signal = pd.Series(False, index=df.index)

        return buy_signal.fillna(False), sell_signal.fillna(False)

    def run_vectorbt_backtest(self, df: pd.DataFrame, strategy: str, params: dict, initial_capital: float = 1000.0, df_htf: Optional[pd.DataFrame] = None) -> dict:
        try:
            import vectorbt as vbt
        except ImportError:
            return {"error": "vectorbt not available"}

        buy_signal, sell_signal = self.compute_signals(df, strategy, params, df_htf=df_htf)
        if buy_signal.sum() == 0 and sell_signal.sum() == 0:
            return {"strategy": strategy, "total_trades": 0, "message": "No signals"}

        portfolio = vbt.Portfolio.from_signals(
            df["close"], buy_signal, sell_signal,
            init_cash=initial_capital, fees=0.00075, slippage=0.001,
        )
        stats = portfolio.stats()
        return {
            "strategy": strategy,
            "total_pnl": float(portfolio.total_return()) * initial_capital,
            "total_pnl_pct": float(portfolio.total_return()) * 100,
            "total_trades": int(stats.get("Total Trades", 0)) if pd.notna(stats.get("Total Trades", 0)) else 0,
            "win_rate": float(stats.get("Win Rate [%]", 0)) if pd.notna(stats.get("Win Rate [%]", 0)) else 0,
            "profit_factor": float(stats.get("Profit Factor", 0)) if pd.notna(stats.get("Profit Factor", 0)) else 0,
            "max_drawdown_pct": float(stats.get("Max Drawdown [%]", 0)) if pd.notna(stats.get("Max Drawdown [%]", 0)) else 0,
            "sharpe_ratio": float(stats.get("Sharpe Ratio", 0)) if pd.notna(stats.get("Sharpe Ratio", 0)) else 0,
        }

    def _param_grid(self, strategy: str) -> dict:
        if strategy == "scalping":
            return {
                "rsi_oversold_strong": [20, 25],
                "rsi_oversold_weak": [35, 40],
                "rsi_overbought_strong": [75, 80],
                "rsi_overbought_weak": [60, 65],
                "bb_position_low": [0.10, 0.20],
                "bb_position_high": [0.80, 0.90],
                "atr_sl_multiplier": [1.0, 1.5],
                "atr_tp_multiplier": [2.0, 3.0],
                "min_score": [2, 3],
            }
        elif strategy == "swing":
            return {
                "rsi_oversold": [25, 30, 35],
                "rsi_overbought": [65, 70, 75],
                "atr_sl_multiplier": [1.0, 1.5, 2.0],
                "atr_tp_multiplier": [2.0, 3.0, 4.0],
            }
        elif strategy == "arbitrage":
            return {
                "bb_position_low": [0.03, 0.05, 0.08],
                "bb_position_high": [0.92, 0.95, 0.97],
                "atr_sl_multiplier": [0.8, 1.0, 1.5],
                "atr_tp_multiplier": [1.5, 2.0, 2.5],
            }
        return {}

    async def run_vectorbt_optimization(self) -> list[dict]:
        try:
            import vectorbt as vbt
        except ImportError:
            logger.warning("vectorbt not available, skipping optimization sweep")
            return []

        strategies = ["scalping", "swing", "arbitrage"]
        symbols = settings.TOP_PAIRS[:2]
        timeframes_map = {"scalping": "5m", "swing": "1h", "arbitrage": "1h"}
        htf_map = {"scalping": "1h", "swing": "1d"}
        all_results = []

        for strategy in strategies:
            tf = timeframes_map[strategy]
            df = await self.fetch_ohlcv_for_sweep(symbols[0], tf, days=30)
            if df is None or len(df) < 50:
                logger.warning(f"No data for {strategy} {symbols[0]} {tf}, skipping sweep")
                continue

            df_htf = None
            if strategy in htf_map:
                df_htf = await self.fetch_ohlcv_for_sweep(symbols[0], htf_map[strategy], days=30)

            param_grid = self._param_grid(strategy)
            keys = list(param_grid.keys())
            values = list(param_grid.values())
            combos = list(product(*values))
            logger.info(f"Vectorbt sweep: {strategy} - {len(combos)} combos on {symbols[0]} {tf}")

            import time as _time
            start = _time.time()
            results = []
            for i, combo in enumerate(combos):
                if _time.time() - start > 120:
                    logger.warning(f"Sweep timeout for {strategy} after {i}/{len(combos)} combos")
                    break
                params = dict(zip(keys, combo))
                try:
                    r = self.run_vectorbt_backtest(df, strategy, params, df_htf=df_htf)
                    r["params"] = params
                    results.append(r)
                except Exception as e:
                    pass

            results = [r for r in results if "error" not in r and r.get("total_trades", 0) > 0]
            results.sort(key=lambda x: x.get("sharpe_ratio", 0), reverse=True)

            if results:
                best = results[0]
                logger.info(
                    f"Best {strategy}: Sharpe={best['sharpe_ratio']:.2f} PnL=${best['total_pnl']:.2f} "
                    f"WR={best['win_rate']:.1f}% params={best['params']}"
                )
                all_results.append({"strategy": strategy, "best": best, "top_5": results[:5]})
            else:
                logger.warning(f"No valid results for {strategy} sweep")

        await self.redis.set_json("evolution:vectorbt_sweep", {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results": all_results,
        })
        return all_results

    async def apply_vectorbt_results(self, sweep_results: list[dict]) -> list[dict]:
        applied = []
        for result in sweep_results:
            strategy = result["strategy"]
            best = result["best"]
            params = best.get("params", {})

            current = await self.redis.get_json(f"strategy:params:{strategy}") or {}
            changed = {k: v for k, v in params.items() if current.get(k) != v}

            if changed:
                await self.redis.set_json(f"strategy:params:{strategy}", {**current, **params})
                applied.append({
                    "type": "vectorbt_optimization",
                    "target": strategy,
                    "params": changed,
                    "reasoning": f"Vectorbt sweep: Sharpe={best['sharpe_ratio']:.2f} PnL=${best['total_pnl']:.2f} WR={best['win_rate']:.1f}%",
                })
                logger.info(f"VECTORBT OPT {strategy}: {changed}")

        return applied

    async def save_pre_change_snapshot(self, snapshot: dict):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        key = f"{SNAPSHOT_PREFIX}{ts}"
        await self.redis.set_json(key, snapshot, ex=86400 * 7)
        current = await self.redis.get_json(CURRENT_PREFIX)
        if current:
            await self.redis.set_json(f"{SNAPSHOT_PREFIX}prev", current, ex=86400 * 7)
        await self.redis.set_json(CURRENT_PREFIX, {
            "timestamp": snapshot["timestamp"],
            "portfolio_value": snapshot.get("portfolio", {}).get("total_value", 0),
            "total_pnl": snapshot.get("portfolio", {}).get("total_pnl", 0),
            "win_rate": snapshot.get("portfolio", {}).get("win_rate", 0),
            "strategy_metrics": snapshot.get("strategy_metrics", []),
            "current_params": snapshot.get("current_params", {}),
        })
        logger.info(f"Saved pre-change snapshot: {key}")

    async def query_qwen_for_changes(self, snapshot: dict) -> Optional[dict]:
        prompt = f"""SYSTEM SNAPSHOT FOR OPTIMIZATION:

PORTFOLIO:
- Total Value: ${snapshot.get('portfolio', {}).get('total_value', 0):.2f}
- Cash: ${snapshot.get('portfolio', {}).get('cash', 0):.2f}
- Total PnL: ${snapshot.get('portfolio', {}).get('total_pnl', 0):.2f}
- Win Rate: {snapshot.get('portfolio', {}).get('win_rate', 0):.1f}%
- Open Positions: {snapshot.get('portfolio', {}).get('open_positions', 0)}
- Fees: ${snapshot.get('portfolio', {}).get('total_fees', 0):.2f}

STRATEGY PERFORMANCE (last 7 days):
{json.dumps(snapshot.get('strategy_metrics', []), indent=2)}

CURRENT PARAMETERS:
{json.dumps(snapshot.get('current_params', {}), indent=2)}

SENTIMENT: {json.dumps(snapshot.get('sentiment', {}), indent=2)}

BACKTEST RESULTS:
{json.dumps(snapshot.get('backtest', {}).get('results', [])[:5], indent=2)}

RECENT FEEDBACK:
{json.dumps(snapshot.get('recent_feedback', []), indent=2)}

Analyze the system performance and propose up to 5 specific changes to improve profitability. Focus on the weakest areas first. Output JSON:"""

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                payload = {
                    "model": self.model,
                    "prompt": prompt,
                    "system": SYSTEM_PROMPT,
                    "stream": False,
                    "options": {"temperature": 0.3, "top_p": 0.9, "num_predict": 2048},
                }
                response = await client.post(f"{self.ollama_host}/api/generate", json=payload)
                response.raise_for_status()
                data = response.json()
                raw = data.get("response", "")
                json_str = self.extract_json(raw)
                if json_str:
                    return json.loads(json_str)
                logger.warning(f"Qwen evolution response not JSON, raw: {raw[:200]}")
        except Exception as e:
            logger.error(f"Qwen evolution query error: {e}")
        return None

    def extract_json(self, raw: str) -> Optional[str]:
        text = raw.strip()
        if not text:
            return None
        if text.startswith("```json"):
            text = text[len("```json"):].strip()
        elif text.startswith("```"):
            text = text[len("```"):].strip()
        if text.endswith("```"):
            text = text[:-3].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            candidate = text[start:end + 1]
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass
        return None

    async def apply_changes(self, proposed: dict) -> list[dict]:
        changes = proposed.get("changes", [])
        applied = []

        for change in changes[:MAX_CHANGES_PER_CYCLE]:
            change_type = change.get("type", "")
            target = change.get("target", "")
            params = change.get("params", {})
            reasoning = change.get("reasoning", "")

            try:
                if change_type == "tuning":
                    await self._apply_tuning(target, params, reasoning)
                    applied.append(change)
                elif change_type == "strategy_config":
                    await self._apply_strategy_config(target, params, reasoning)
                    applied.append(change)
                elif change_type == "risk":
                    await self._apply_risk(params, reasoning)
                    applied.append(change)
                elif change_type == "new_strategy":
                    success = await self._apply_new_strategy(params, reasoning)
                    if success:
                        applied.append(change)
                else:
                    logger.warning(f"Unknown change type: {change_type}")
            except Exception as e:
                logger.error(f"Error applying change {change_type}/{target}: {e}")

        return applied

    async def _apply_tuning(self, strategy: str, params: dict, reasoning: str):
        key = f"strategy:params:{strategy}"
        current = await self.redis.get_json(key) or {}
        current.update(params)
        await self.redis.set_json(key, current)
        logger.info(f"TUNING {strategy}: {params} — {reasoning}")

    async def _apply_strategy_config(self, strategy: str, params: dict, reasoning: str):
        key = f"strategy:config:{strategy}"
        current = await self.redis.get_json(key) or {}
        current.update(params)
        await self.redis.set_json(key, current)
        logger.info(f"CONFIG {strategy}: {params} — {reasoning}")

    async def _apply_risk(self, params: dict, reasoning: str):
        if "max_position_pct" in params:
            val = float(params["max_position_pct"])
            if val > 0.30:
                params["max_position_pct"] = 0.30
                logger.warning("Clamped max_position_pct to 0.30")
        if "max_drawdown_pct" in params:
            val = float(params["max_drawdown_pct"])
            if val > 0.20:
                params["max_drawdown_pct"] = 0.20
                logger.warning("Clamped max_drawdown_pct to 0.20")
        key = "risk:params"
        current = await self.redis.get_json(key) or {}
        current.update(params)
        await self.redis.set_json(key, current)
        logger.info(f"RISK: {params} — {reasoning}")

    async def _apply_new_strategy(self, params: dict, reasoning: str) -> bool:
        name = params.get("name", "")
        code = params.get("code", "")
        timeframes = params.get("timeframes", ["1h"])
        strategy_params = params.get("params", {})

        if not name or not code:
            logger.warning("New strategy missing name or code, skipping")
            return False

        if not name.replace("_", "").replace("-", "").isalnum():
            logger.warning(f"Invalid strategy name: {name}")
            return False

        try:
            compile(code, f"strategy_{name}", "exec")
        except SyntaxError as e:
            logger.warning(f"New strategy {name} has syntax error: {e}")
            return False

        strategy_dir = f"/app/data/strategies/{name}"
        os.makedirs(strategy_dir, exist_ok=True)

        with open(f"{strategy_dir}/agent.py", "w") as f:
            f.write(code)

        with open(f"{strategy_dir}/params.json", "w") as f:
            json.dump({"timeframes": timeframes, **strategy_params}, f, indent=2)

        await self.redis.set_json(f"strategy:params:{name}", strategy_params)
        await self.redis.set_json(f"strategy:config:{name}", {
            "active": True,
            "cooldown_seconds": 1800,
            "confidence_weight": 1.0,
        })

        logger.info(f"NEW STRATEGY {name}: timeframes={timeframes} — {reasoning}")
        logger.info(f"Strategy code saved to {strategy_dir}/agent.py (requires container rebuild to activate)")
        return True

    async def check_rollback(self) -> bool:
        current = await self.redis.get_json(CURRENT_PREFIX)
        if not current:
            return False

        prev_key = f"{SNAPSHOT_PREFIX}prev"
        prev = await self.redis.get_json(prev_key)
        if not prev:
            logger.info("No previous snapshot for rollback comparison")
            return False

        current_value = float(current.get("portfolio_value", 0))
        prev_value = float(prev.get("portfolio_value", 0))
        current_pnl = float(current.get("total_pnl", 0))
        prev_pnl = float(prev.get("total_pnl", 0))
        current_wr = float(current.get("win_rate", 0))
        prev_wr = float(prev.get("win_rate", 0))

        needs_rollback = False
        reasons = []

        if prev_value > 0:
            pnl_change = (current_pnl - prev_pnl) / abs(prev_value) if prev_value else 0
            if pnl_change < ROLLBACK_PNL_THRESHOLD:
                needs_rollback = True
                reasons.append(f"PnL deteriorated by {pnl_change*100:.1f}% (threshold: {ROLLBACK_PNL_THRESHOLD*100:.1f}%)")

        wr_change = (current_wr - prev_wr) / 100
        if wr_change < ROLLBACK_WINRATE_THRESHOLD:
            needs_rollback = True
            reasons.append(f"Win rate dropped by {wr_change*100:.1f}% (threshold: {ROLLBACK_WINRATE_THRESHOLD*100:.1f}%)")

        if needs_rollback:
            logger.warning(f"ROLLBACK TRIGGERED: {'; '.join(reasons)}")
            await self._execute_rollback(prev)
            return True

        logger.info(f"Rollback check passed: PnL change={current_pnl - prev_pnl:.2f}, WR change={current_wr - prev_wr:.1f}%")
        return False

    async def _execute_rollback(self, prev_snapshot: dict):
        prev_params = prev_snapshot.get("current_params", {})

        for strategy, params in prev_params.items():
            if strategy == "risk_manager":
                await self.redis.set_json("risk:params", params)
                logger.info(f"ROLLBACK risk:params restored")
            else:
                tuning = {k: v for k, v in params.items() if k in [
                    "rsi_overbought", "rsi_oversold", "macd_threshold",
                    "bb_width_threshold", "bb_position_threshold",
                    "ema_diff_threshold", "atr_sl_multiplier", "atr_tp_multiplier",
                ]}
                config = {k: v for k, v in params.items() if k in [
                    "active", "cooldown_seconds", "confidence_weight", "max_positions_per_strategy",
                ]}
                if tuning:
                    await self.redis.set_json(f"strategy:params:{strategy}", tuning)
                if config:
                    await self.redis.set_json(f"strategy:config:{strategy}", config)
                logger.info(f"ROLLBACK {strategy} params restored")

        await self.redis.set_json("evolution:rollback", {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "restored_from": prev_snapshot.get("timestamp", ""),
            "reason": "Automatic rollback due to performance deterioration",
        })
        logger.info("Rollback completed successfully")

    async def log_evolution_cycle(self, snapshot: dict, proposed: Optional[dict], applied: list[dict]):
        cycle = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "snapshot_summary": {
                "portfolio_value": snapshot.get("portfolio", {}).get("total_value", 0),
                "pnl": snapshot.get("portfolio", {}).get("total_pnl", 0),
                "win_rate": snapshot.get("portfolio", {}).get("win_rate", 0),
            },
            "proposed_count": len(proposed.get("changes", [])) if proposed else 0,
            "applied_count": len(applied),
            "applied_changes": applied,
            "analysis": proposed.get("analysis", "") if proposed else "",
        }

        history_raw = await self.redis.get("evolution:history_raw") or "[]"
        history = json.loads(history_raw)
        history.append(cycle)
        if len(history) > 100:
            history = history[-100:]
        await self.redis.set("evolution:history_raw", json.dumps(history, default=str), ex=86400 * 30)

        await self.redis.set_json("evolution:last_cycle", cycle)
        logger.info(f"Evolution cycle complete: {len(applied)} changes applied")

    async def run_cycle(self):
        logger.info("=== Starting evolution cycle ===")
        snapshot = await self.collect_system_snapshot()
        logger.info(
            f"System snapshot: value=${snapshot.get('portfolio', {}).get('total_value', 0):.2f} "
            f"pnl=${snapshot.get('portfolio', {}).get('total_pnl', 0):.2f} "
            f"wr={snapshot.get('portfolio', {}).get('win_rate', 0):.1f}%"
        )

        rolled_back = await self.check_rollback()
        if rolled_back:
            logger.info("Skipping new changes this cycle due to rollback")
            await self.log_evolution_cycle(snapshot, None, [{"type": "rollback", "reason": "performance deterioration"}])
            return

        await self.save_pre_change_snapshot(snapshot)

        vectorbt_results = await self.run_vectorbt_optimization()
        vectorbt_applied = []
        if vectorbt_results:
            vectorbt_applied = await self.apply_vectorbt_results(vectorbt_results)
            logger.info(f"Vectorbt optimization applied {len(vectorbt_applied)} changes")

        proposed = await self.query_qwen_for_changes(snapshot)
        if not proposed:
            logger.info("No changes proposed by Qwen this cycle")
            await self.log_evolution_cycle(snapshot, None, [])
            return

        logger.info(f"Qwen proposed {len(proposed.get('changes', []))} changes: {proposed.get('analysis', '')}")

        applied = await self.apply_changes(proposed)
        all_applied = vectorbt_applied + applied
        await self.log_evolution_cycle(snapshot, proposed, all_applied)

    async def run(self):
        self.running = True
        await self.initialize()
        logger.info(f"Evolution Agent running (cycle every {CYCLE_INTERVAL}s = {CYCLE_INTERVAL//3600}h)")

        while self.running:
            try:
                await self.run_cycle()
                await asyncio.sleep(CYCLE_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Evolution cycle error: {e}")
                await asyncio.sleep(600)


async def main():
    agent = EvolutionAgent()
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
