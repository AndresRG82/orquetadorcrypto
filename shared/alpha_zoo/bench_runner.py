import logging
import time
from typing import Optional

import numpy as np
import pandas as pd

from shared.alpha_zoo.factor_analysis import compute_ic_series

logger = logging.getLogger("alpha_zoo.bench")


def _load_universe_panel(universe: str, start: str, end: str) -> dict:
    raise NotImplementedError(
        "Panel loading from TimescaleDB must be wired per-deployment. "
        "Implement this function to return a dict of wide DataFrames: "
        '{"open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}'
    )


def _compute_forward_returns(panel: dict) -> pd.DataFrame:
    close = panel["close"]
    returns = close.pct_change().shift(-1)
    return returns


def run_bench(
    registry,
    zoo: str = "academic",
    universe: str = "crypto",
    period: tuple[str, str] = ("2025-01-01", "2026-06-23"),
    top: int = 20,
    only: Optional[list[str]] = None,
) -> dict:
    t0 = time.time()
    alpha_ids = registry.list(zoo=zoo, universe=universe)
    if only:
        alpha_ids = [a for a in alpha_ids if a.split("_", 1)[1] in only]
    if not alpha_ids:
        return {
            "status": "error",
            "message": f"No alphas for zoo={zoo} universe={universe}",
        }
    panel = _load_universe_panel(universe, period[0], period[1])
    forward_returns = _compute_forward_returns(panel)
    rows = []
    skipped = []
    for alpha_id in alpha_ids:
        if top and len(rows) >= top:
            break
        try:
            factor = registry.compute(alpha_id, panel)
            ic_series = compute_ic_series(factor, forward_returns)
            ic_clean = ic_series.dropna()
            if len(ic_clean) < 5:
                skipped.append({"id": alpha_id, "reason": "insufficient data"})
                continue
            ic_mean = ic_clean.mean()
            ic_std = ic_clean.std()
            ic_ir = ic_mean / ic_std if ic_std > 0 else 0.0
            ic_positive = (ic_clean > 0).mean()
            t_stat = ic_mean / (ic_std / np.sqrt(len(ic_clean))) if ic_std > 0 else 0.0
            if ic_mean > 0.02 and ic_positive >= 0.55 and abs(t_stat) > 2:
                status = "alive"
            elif ic_mean < -0.02 and abs(t_stat) > 2:
                status = "reversed"
            else:
                status = "dead"
            rows.append({
                "id": alpha_id,
                "ic_mean": round(ic_mean, 4),
                "ic_std": round(ic_std, 4),
                "ic_ir": round(ic_ir, 4),
                "ic_positive_ratio": round(ic_positive, 4),
                "t_stat": round(t_stat, 4),
                "status": status,
            })
        except Exception as e:
            skipped.append({"id": alpha_id, "reason": str(e)[:100]})
    rows.sort(key=lambda r: abs(r["ic_ir"]), reverse=True)
    by_theme = {}
    for r in rows:
        for t in registry.get(r["id"]).meta.theme if registry.get(r["id"]) else []:
            by_theme.setdefault(t, {"alive": 0, "dead": 0, "reversed": 0})
            by_theme[t][r["status"]] += 1
    return {
        "status": "ok",
        "zoo": zoo,
        "universe": universe,
        "period": period,
        "n_alphas_tested": len(rows),
        "n_skipped": len(skipped),
        "alive": [r for r in rows if r["status"] == "alive"],
        "dead": [r for r in rows if r["status"] == "dead"],
        "reversed": [r for r in rows if r["status"] == "reversed"],
        "by_theme": by_theme,
        "top5_by_ir": rows[:5],
        "skipped": skipped[:5],
        "wall_seconds": round(time.time() - t0, 2),
    }


def run_bench_strict(
    registry,
    zoo: str = "academic",
    universe: str = "crypto",
    period: tuple[str, str] = ("2025-01-01", "2026-06-23"),
    *,
    random_control: int = 5,
    oos_split: Optional[float] = None,
    alpha_t_threshold: float = 2.0,
) -> dict:
    if random_control is None:
        raise TypeError("random_control is required (keyword-only)")
    result = run_bench(registry, zoo=zoo, universe=universe, period=period)
    if result["status"] != "ok":
        return result
    panel = _load_universe_panel(universe, period[0], period[1])
    forward_returns = _compute_forward_returns(panel)
    confirmed = []
    for alpha_row in result["alive"]:
        alpha_id = alpha_row["id"]
        factor = registry.compute(alpha_id, panel)
        ic_real = compute_ic_series(factor, forward_returns).dropna()
        random_ics = []
        for seed in range(random_control):
            shuffled = factor.apply(
                lambda col: col.sample(frac=1).values,
                axis=0,
            )
            shuffled.index = factor.index
            ic_rand = compute_ic_series(shuffled, forward_returns).dropna()
            random_ics.append(ic_rand.mean())
        random_mean = np.mean(random_ics) if random_ics else 0.0
        random_std = np.std(random_ics, ddof=1) if len(random_ics) > 1 else 1.0
        alpha_t = (ic_real.mean() - random_mean) / (random_std / np.sqrt(len(ic_real)))
        if abs(alpha_t) > alpha_t_threshold:
            confirmed.append({**alpha_row, "alpha_t": round(alpha_t, 4)})
    result["confirmed_alive"] = confirmed
    result["n_confirmed"] = len(confirmed)
    result["random_control"] = {
        "n_seeds": random_control,
        "alpha_t_threshold": alpha_t_threshold,
    }
    return result
