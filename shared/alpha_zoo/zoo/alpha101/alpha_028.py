import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_corr

__alpha_meta__ = {
    "id": "alpha101_028",
    "nickname": "Alpha #28 (Kakushadze)",
    "theme": ["reversal"],
    "formula_latex": "correlation(rank(high), rank(volume), 8)",
    "columns_required": ["high", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    high = panel["high"]
    volume = panel["volume"]
    return rank(high).rolling(8).corr(rank(volume))
