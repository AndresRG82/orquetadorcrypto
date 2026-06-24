import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_cov

__alpha_meta__ = {
    "id": "alpha101_003",
    "nickname": "Alpha #3 (Kakushadze)",
    "theme": ["reversal"],
    "formula_latex": "-1 * correlation(rank(open), rank(volume), 10)",
    "columns_required": ["open", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    open_p = panel["open"]
    volume = panel["volume"]
    return -rank(open_p).rolling(10).corr(rank(volume))
