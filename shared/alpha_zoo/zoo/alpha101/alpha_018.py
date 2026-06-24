import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_corr, ts_rank, ts_cov, safe_div

__alpha_meta__ = {
    "id": "alpha101_018",
    "nickname": "Alpha #18 (Kakushadze)",
    "theme": ["reversal"],
    "formula_latex": "correlation(rank(close), rank(open), 5)",
    "columns_required": ["open", "close"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    open_p = panel["open"]
    return rank(close).rolling(5).corr(rank(open_p))
