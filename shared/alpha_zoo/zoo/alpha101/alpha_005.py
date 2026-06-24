import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_cov, ts_corr

__alpha_meta__ = {
    "id": "alpha101_005",
    "nickname": "Alpha #5 (Kakushadze)",
    "theme": ["reversal"],
    "formula_latex": "-1 * correlation(rank((open - sum(high, 6) / 6 + low)), rank(volume), 4)",
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
    inner = open_p - high.rolling(6).sum() / 6 + low
    return -rank(inner).rolling(4).corr(rank(volume))
