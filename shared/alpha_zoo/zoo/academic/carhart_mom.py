import pandas as pd

from shared.alpha_zoo.base import safe_div, delta

__alpha_meta__ = {
    "id": "academic_carhart_mom",
    "nickname": "Carhart Momentum (UMD Price Proxy)",
    "theme": ["momentum"],
    "formula_latex": r"zscore(ret\_12m - ret\_1m)",
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 252,
    "min_warmup_bars": 253,
    "notes": "[PRICE PROXY] 12-month minus 1-month return, skip most recent month",
}


def _cross_sectional_zscore(df: pd.DataFrame) -> pd.DataFrame:
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    centered = df.sub(mean, axis=0)
    return centered.div(std.where(std > 0), axis=0).replace([float("inf"), float("-inf")], float("nan"))


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    ret_long = safe_div(delta(close, 252), close.shift(252))
    ret_short = safe_div(delta(close, 21), close.shift(21))
    return _cross_sectional_zscore(ret_long - ret_short)
