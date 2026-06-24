import numpy as np
import pandas as pd
from shared.alpha_zoo.base import rank, delta, ts_corr, ts_cov, safe_div, ts_max, ts_min, ts_mean, ts_std, signed_power, ts_rank, ts_argmax, ts_argmin, decay_linear

__alpha_meta__ = {
        "id": 'alpha101_099',
        "nickname": 'Alpha #99',
        "theme": ['momentum'],
        "formula_latex": 'correlation(rank(close), rank(volume), 9) * -1',
        "columns_required": ['close', 'volume'],
        "universe": ['crypto'],
        "frequency": ['1d'],
        "min_warmup_bars": 20,
    }

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    close = panel["close"]
    volume = panel["volume"]
    return -rank(close).rolling(9).corr(rank(volume))
