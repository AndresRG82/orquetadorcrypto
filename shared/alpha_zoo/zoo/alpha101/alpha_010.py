import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_cov, ts_corr, signed_power

__alpha_meta__ = {
    "id": "alpha101_010",
    "nickname": "Alpha #10 (Kakushadze)",
    "theme": ["reversal"],
    "formula_latex": "correlation(rank(volume), rank(high), 5)",
    "columns_required": ["high", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    high = panel["high"]
    volume = panel["volume"]
    return rank(volume).rolling(5).corr(rank(high))
