import numpy as np
import pandas as pd

from shared.alpha_zoo.base import ts_mean, safe_div

__alpha_meta__ = {
    "id": "academic_smb",
    "nickname": "Small Minus Big (Price Proxy)",
    "theme": ["quality"],
    "formula_latex": r"-log(ts\_mean(dvol, 60))",
    "columns_required": ["close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 60,
    "min_warmup_bars": 61,
    "notes": "[PRICE PROXY] Smaller caps tend to have lower dollar volume",
}


def _cross_sectional_zscore(df: pd.DataFrame) -> pd.DataFrame:
    mean = df.mean(axis=1, skipna=True)
    std = df.std(axis=1, ddof=1, skipna=True)
    centered = df.sub(mean, axis=0)
    return centered.div(std.where(std > 0), axis=0).replace([np.inf, -np.inf], np.nan)


def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    volume = panel["volume"]
    dvol = close * volume
    avg_dvol = ts_mean(dvol, 60)
    proxy = -np.log(avg_dvol.replace(0, np.nan))
    return _cross_sectional_zscore(proxy)
