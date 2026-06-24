import numpy as np
import pandas as pd
from shared.alpha_zoo.base import ts_argmax, signed_power, ts_std, rank

__alpha_meta__ = {
    "id": "alpha101_001",
    "nickname": "Alpha #1 (Kakushadze)",
    "theme": ["reversal", "volatility"],
    "formula_latex": r"rank(ts\_argmax(signed\_power((returns<0)?std(returns,20):close, 2), 5)) - 0.5",
    "columns_required": ["close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "decay_horizon": 5,
    "min_warmup_bars": 25,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    returns = close.pct_change()
    cond = (returns < 0).astype(float)
    x = ts_std(returns, 20) * cond + close * (1.0 - cond)
    out = rank(ts_argmax(signed_power(x, 2.0), 5)) - 0.5
    return out
