import pandas as pd

from shared.alpha_zoo.base import ts_cov

__alpha_meta__ = {
    "id": "academic_retskew",
    "nickname": "Return Skewness (Harvey-Siddique 2000)",
    "theme": ["volatility"],
    "formula_latex": "zscore(-skew(ret, 60))",
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 60,
    "min_warmup_bars": 61,
    "notes": "[PRICE PROXY] Negative skewness premium, Harvey & Siddique (2000)",
}


def _cross_sectional_zscore(df: pd.DataFrame) -> pd.DataFrame:
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    centered = df.sub(mean, axis=0)
    return centered.div(std.where(std > 0), axis=0).replace([float("inf"), float("-inf")], float("nan"))


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    daily_ret = close.pct_change()
    skewness = daily_ret.rolling(window=60, min_periods=60).skew()
    return _cross_sectional_zscore(-skewness)
