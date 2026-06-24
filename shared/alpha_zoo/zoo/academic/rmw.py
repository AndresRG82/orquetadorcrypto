import pandas as pd

from shared.alpha_zoo.base import ts_std

__alpha_meta__ = {
    "id": "academic_rmw",
    "nickname": "Robust Minus Weak (Price Proxy)",
    "theme": ["volatility", "quality"],
    "formula_latex": r"zscore(-ts\_std(ret, 60))",
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 60,
    "min_warmup_bars": 61,
    "notes": "[PRICE PROXY] Low-volatility anomaly overlapping with profitability quality",
}


def _cross_sectional_zscore(df: pd.DataFrame) -> pd.DataFrame:
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    centered = df.sub(mean, axis=0)
    return centered.div(std.where(std > 0), axis=0).replace([float("inf"), float("-inf")], float("nan"))


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    daily_ret = close.pct_change()
    vol = ts_std(daily_ret, 60)
    proxy = -vol
    return _cross_sectional_zscore(proxy)
