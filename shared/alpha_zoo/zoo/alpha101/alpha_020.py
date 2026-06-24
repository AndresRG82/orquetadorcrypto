import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_corr, ts_rank, ts_cov, safe_div, signed_power

__alpha_meta__ = {
    "id": "alpha101_020",
    "nickname": "Alpha #20 (Kakushadze)",
    "theme": ["reversal"],
    "formula_latex": "correlation(rank(close), rank(open), 7) * -1",
    "columns_required": ["open", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    open_p = panel["open"]
    return -rank(close).rolling(7).corr(rank(open_p))
