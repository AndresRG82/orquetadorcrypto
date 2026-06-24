import pandas as pd

from shared.alpha_zoo.base import safe_div, ts_mean

__alpha_meta__ = {
    "id": "academic_illiq",
    "nickname": "Amihud Illiquidity (2002)",
    "theme": ["liquidity"],
    "formula_latex": r"zscore(ts\_mean(|ret| / dvol, 21))",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 21,
    "min_warmup_bars": 22,
    "notes": "[PRICE PROXY] Absolute return per dollar of volume, Amihud (2002)",
}


def _cross_sectional_zscore(df: pd.DataFrame) -> pd.DataFrame:
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    centered = df.sub(mean, axis=0)
    return centered.div(std.where(std > 0), axis=0).replace([float("inf"), float("-inf")], float("nan"))


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    volume = panel["volume"]
    daily_ret = close.pct_change()
    dollar_volume = close * volume
    illiq = safe_div(daily_ret.abs(), dollar_volume)
    return _cross_sectional_zscore(ts_mean(illiq, 21))
