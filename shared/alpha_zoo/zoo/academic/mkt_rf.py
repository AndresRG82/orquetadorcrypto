import numpy as np
import pandas as pd

from shared.alpha_zoo.base import safe_div, ts_mean, rank, scale

__alpha_meta__ = {
    "id": "academic_mkt_rf",
    "nickname": "Market Risk Premium (Price Proxy)",
    "theme": ["momentum"],
    "formula_latex": r"zscore(ts\_mean(ret, 21))",
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 21,
    "min_warmup_bars": 22,
    "notes": "[PRICE PROXY] Cross-sectional z-score of 21-day total return",
}


def _cross_sectional_zscore(df: pd.DataFrame) -> pd.DataFrame:
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    centered = df.sub(mean, axis=0)
    return centered.div(std.where(std > 0), axis=0).replace([np.inf, -np.inf], np.nan)


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    ret = close.pct_change(periods=21)
    return _cross_sectional_zscore(ret)
