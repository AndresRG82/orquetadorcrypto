import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_cov, ts_corr, signed_power

__alpha_meta__ = {
    "id": "alpha101_007",
    "nickname": "Alpha #7 (Kakushadze)",
    "theme": ["reversal"],
    "formula_latex": "correlation(rank((volume * 3) / ((open + close - high - low) / 2 + volume)), rank(volume), 4) * -1",
    "columns_required": ["open", "high", "low", "close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    open_p = panel["open"]
    high = panel["high"]
    low = panel["low"]
    close = panel["close"]
    volume = panel["volume"]
    denom = (open_p + close - high - low) / 2 + volume
    inner = (volume * 3) / denom.replace(0, np.nan)
    return -rank(inner).rolling(4).corr(rank(volume))
