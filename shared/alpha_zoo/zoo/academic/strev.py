import pandas as pd

from shared.alpha_zoo.base import safe_div, delta

__alpha_meta__ = {
    "id": "academic_strev",
    "nickname": "Short-Term Reversal (Jegadeesh 1990)",
    "theme": ["reversal"],
    "formula_latex": r"zscore(-ret\_1m)",
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 21,
    "min_warmup_bars": 22,
    "notes": "[PRICE PROXY] One-month reversal, Jegadeesh (1990)",
}


def _cross_sectional_zscore(df: pd.DataFrame) -> pd.DataFrame:
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    centered = df.sub(mean, axis=0)
    return centered.div(std.where(std > 0), axis=0).replace([float("inf"), float("-inf")], float("nan"))


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    ret_1m = safe_div(delta(close, 21), close.shift(21))
    return _cross_sectional_zscore(-ret_1m)
