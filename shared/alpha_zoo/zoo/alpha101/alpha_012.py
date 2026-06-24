import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_corr

__alpha_meta__ = {
    "id": "alpha101_012",
    "nickname": "Alpha #12 (Kakushadze)",
    "theme": ["momentum"],
    "formula_latex": "correlation(rank(volume), rank(low), 3) * -1",
    "columns_required": ["low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    low = panel["low"]
    volume = panel["volume"]
    return -rank(volume).rolling(3).corr(rank(low))
