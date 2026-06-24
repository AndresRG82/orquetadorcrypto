import numpy as np
import pandas as pd

from shared.alpha_zoo.base import ts_mean, delta

__alpha_meta__ = {
    "id": "academic_cma",
    "nickname": "Conservative Minus Aggressive (Price Proxy)",
    "theme": ["quality"],
    "formula_latex": r"zscore(-delta(log(ts\_mean(vol, 60)), 60))",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 120,
    "min_warmup_bars": 121,
    "notes": "[PRICE PROXY] Volume contraction as conservative investment proxy",
}


def _cross_sectional_zscore(df: pd.DataFrame) -> pd.DataFrame:
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    centered = df.sub(mean, axis=0)
    return centered.div(std.where(std > 0), axis=0).replace([np.inf, -np.inf], np.nan)


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    volume = panel["volume"]
    avg_vol = ts_mean(volume, 60)
    proxy = -delta(np.log(avg_vol.replace(0, np.nan)), 60)
    return _cross_sectional_zscore(proxy)
