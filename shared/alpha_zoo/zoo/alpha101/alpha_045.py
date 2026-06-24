import numpy as np
import pandas as pd
from shared.alpha_zoo.base import rank, delta, ts_corr, ts_cov, safe_div, ts_max, ts_min, ts_mean, ts_std, signed_power, ts_rank, ts_argmax, ts_argmin, decay_linear

__alpha_meta__ = {
        "id": 'alpha101_045',
        "nickname": 'Alpha #45',
        "theme": ['reversal'],
        "formula_latex": 'correlation(rank(high), rank(volume), 7) * -1',
        "columns_required": ['high', 'volume'],
        "universe": ['crypto'],
        "frequency": ['1d'],
        "min_warmup_bars": 20,
    }

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    high = panel["high"]
    volume = panel["volume"]
    return -rank(high).rolling(7).corr(rank(volume))
