import numpy as np
import pandas as pd
from shared.alpha_zoo.base import rank, delta, ts_corr, ts_cov, safe_div, ts_max, ts_min, ts_mean, ts_std, signed_power, ts_rank, ts_argmax, ts_argmin, decay_linear

__alpha_meta__ = {
        "id": 'alpha101_049',
        "nickname": 'Alpha #49',
        "theme": ['reversal'],
        "formula_latex": 'correlation(rank(low), rank(volume), 3)',
        "columns_required": ['low', 'volume'],
        "universe": ['crypto'],
        "frequency": ['1d'],
        "min_warmup_bars": 20,
    }

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    low = panel["low"]
    volume = panel["volume"]
    return rank(low).rolling(3).corr(rank(volume))
