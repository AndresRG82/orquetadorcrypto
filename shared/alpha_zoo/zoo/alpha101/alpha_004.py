import numpy as np
from shared.alpha_zoo.base import rank, delta, ts_cov

__alpha_meta__ = {
    "id": "alpha101_004",
    "nickname": "Alpha #4 (Kakushadze)",
    "theme": ["reversal"],
    "formula_latex": r"-1 * Ts\_Rank(rank(low), 9)",
    "columns_required": ["low"],
    "universe": ["crypto"],
    "frequency": ["1d"],
    "min_warmup_bars": 20,
}

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    low = panel["low"]
    return -rank(low).rolling(9).rank(pct=True)
