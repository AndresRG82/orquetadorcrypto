import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_corr

__alpha_meta__ = {
    "id": "alpha101_013",
    "nickname": "Alpha #13 (Kakushadze)",
    "theme": ["momentum"],
    "formula_latex": "correlation(rank(open), rank(volume), 5)",
    "columns_required": ["open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    open_p = panel["open"]
    volume = panel["volume"]
    return rank(open_p).rolling(5).corr(rank(volume))
