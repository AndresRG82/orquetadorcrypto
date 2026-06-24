import numpy as np
from shared.alpha_zoo.base import rank, delta, safe_div

__alpha_meta__ = {
    "id": "alpha101_002",
    "nickname": "Alpha #2 (Kakushadze)",
    "theme": ["reversal"],
    "formula_latex": "-1 * correlation(rank(delta(log(volume), 2)), rank(((close - open) / open)), 6)",
    "columns_required": ["open", "close", "volume"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 12,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    open_p = panel["open"]
    close = panel["close"]
    volume = panel["volume"]
    inner = rank(delta(np.log(volume), 2))
    outer = rank(safe_div(close - open_p, open_p))
    return -inner.rolling(6).corr(outer)
