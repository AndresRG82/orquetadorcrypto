import pandas as pd

from shared.alpha_zoo.base import safe_div, ts_max

__alpha_meta__ = {
    "id": "academic_high52w",
    "nickname": "52-Week High (George-Hwang 2004)",
    "theme": ["momentum"],
    "formula_latex": r"zscore(close / ts\_max(close, 252))",
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 252,
    "min_warmup_bars": 253,
    "notes": "[PRICE PROXY] Closeness to 52-week high, George & Hwang (2004)",
}


def _cross_sectional_zscore(df: pd.DataFrame) -> pd.DataFrame:
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    centered = df.sub(mean, axis=0)
    return centered.div(std.where(std > 0), axis=0).replace([float("inf"), float("-inf")], float("nan"))


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    high_52w = ts_max(close, 252)
    ratio = safe_div(close, high_52w)
    return _cross_sectional_zscore(ratio)
