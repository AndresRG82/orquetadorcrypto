import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_corr

__alpha_meta__ = {
    "id": "alpha101_011",
    "nickname": "Alpha #11 (Kakushadze)",
    "theme": ["momentum"],
    "formula_latex": "correlation(rank(close), rank(open), 8) * -1",
    "columns_required": ["open", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    open_p = panel["open"]
    return -rank(close).rolling(8).corr(rank(open_p))
