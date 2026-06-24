import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_corr

__alpha_meta__ = {
    "id": "alpha101_014",
    "nickname": "Alpha #14 (Kakushadze)",
    "theme": ["momentum"],
    "formula_latex": "correlation(rank(open), rank(volume), 10)",
    "columns_required": ["open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    open_p = panel["open"]
    volume = panel["volume"]
    return rank(open_p).rolling(10).corr(rank(volume))
