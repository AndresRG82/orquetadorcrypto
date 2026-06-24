import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_corr

__alpha_meta__ = {
    "id": "alpha101_022",
    "nickname": "Alpha #22 (Kakushadze)",
    "theme": ["reversal"],
    "formula_latex": "correlation(rank(close), rank(open), 3)",
    "columns_required": ["open", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    open_p = panel["open"]
    return rank(close).rolling(3).corr(rank(open_p))
