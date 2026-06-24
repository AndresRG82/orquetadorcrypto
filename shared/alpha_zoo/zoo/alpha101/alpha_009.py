import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_cov, ts_corr, signed_power

__alpha_meta__ = {
    "id": "alpha101_009",
    "nickname": "Alpha #9 (Kakushadze)",
    "theme": ["reversal"],
    "formula_latex": "-1 * correlation(rank((low + high) / 2), rank(volume), 7)",
    "columns_required": ["high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    high = panel["high"]
    low = panel["low"]
    volume = panel["volume"]
    inner = (low + high) / 2
    return -rank(inner).rolling(7).corr(rank(volume))
