import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_corr, ts_max, safe_div

__alpha_meta__ = {
    "id": "alpha101_024",
    "nickname": "Alpha #24 (Kakushadze)",
    "theme": ["reversal", "volatility"],
    "formula_latex": "correlation(rank(high), rank(volume), 2)",
    "columns_required": ["high", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    high = panel["high"]
    volume = panel["volume"]
    return rank(high).rolling(2).corr(rank(volume))
