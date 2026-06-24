import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_corr

__alpha_meta__ = {
    "id": "alpha101_029",
    "nickname": "Alpha #29 (Kakushadze)",
    "theme": ["reversal"],
    "formula_latex": "correlation(rank(high), rank(volume), 9)",
    "columns_required": ["high", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    high = panel["high"]
    volume = panel["volume"]
    return rank(high).rolling(9).corr(rank(volume))
