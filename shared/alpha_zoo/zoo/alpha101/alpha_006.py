import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_cov, ts_corr, signed_power

__alpha_meta__ = {
    "id": "alpha101_006",
    "nickname": "Alpha #6 (Kakushadze)",
    "theme": ["reversal"],
    "formula_latex": "-1 * correlation(rank((open + high - low) / 2), rank(volume), 4)",
    "columns_required": ["open", "high", "low", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    open_p = panel["open"]
    high = panel["high"]
    low = panel["low"]
    volume = panel["volume"]
    inner = (open_p + high - low) / 2
    return -rank(inner).rolling(4).corr(rank(volume))
