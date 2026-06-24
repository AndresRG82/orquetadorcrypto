import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_corr, ts_rank, ts_cov

__alpha_meta__ = {
    "id": "alpha101_017",
    "nickname": "Alpha #17 (Kakushadze)",
    "theme": ["momentum"],
    "formula_latex": "correlation(rank(volume), rank(low), 5) * -1",
    "columns_required": ["low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    low = panel["low"]
    volume = panel["volume"]
    return -rank(volume).rolling(5).corr(rank(low))
