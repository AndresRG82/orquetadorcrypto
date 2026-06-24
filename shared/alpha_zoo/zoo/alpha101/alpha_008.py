import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_cov, ts_corr, signed_power

__alpha_meta__ = {
    "id": "alpha101_008",
    "nickname": "Alpha #8 (Kakushadze)",
    "theme": ["reversal"],
    "formula_latex": "-1 * correlation(rank(high), rank(volume), 5)",
    "columns_required": ["high", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    high = panel["high"]
    volume = panel["volume"]
    return -rank(high).rolling(5).corr(rank(volume))
