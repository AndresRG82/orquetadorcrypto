import pandas as pd

from shared.alpha_zoo.base import delta, safe_div

__alpha_meta__ = {
    "id": "academic_hml",
    "nickname": "High Minus Low (Price Proxy)",
    "theme": ["value", "reversal"],
    "formula_latex": "zscore(-(close - close.shift(252)) / close.shift(252))",
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 252,
    "min_warmup_bars": 253,
    "notes": "[PRICE PROXY] Negative long-term return as value proxy",
}


def _cross_sectional_zscore(df: pd.DataFrame) -> pd.DataFrame:
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    centered = df.sub(mean, axis=0)
    return centered.div(std.where(std > 0), axis=0).replace([float("inf"), float("-inf")], float("nan"))


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    long_term_ret = safe_div(delta(close, 252), close.shift(252))
    proxy = -long_term_ret
    return _cross_sectional_zscore(proxy)
