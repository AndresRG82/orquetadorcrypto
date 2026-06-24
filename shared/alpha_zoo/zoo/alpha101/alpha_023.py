import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_corr, ts_max, safe_div

__alpha_meta__ = {
    "id": "alpha101_023",
    "nickname": "Alpha #23 (Kakushadze)",
    "theme": ["reversal", "volatility"],
    "formula_latex": "correlation(rank(high), rank(volume), 5) * -1",
    "columns_required": ["high", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    high = panel["high"]
    volume = panel["volume"]
    return -rank(high).rolling(5).corr(rank(volume))
