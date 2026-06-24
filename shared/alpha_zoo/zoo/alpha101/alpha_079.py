import numpy as np
import pandas as pd
from shared.alpha_zoo.base import rank, delta, ts_corr, ts_cov, safe_div, ts_max, ts_min, ts_mean, ts_std, signed_power, ts_rank, ts_argmax, ts_argmin, decay_linear

__alpha_meta__ = {
        "id": 'alpha101_079',
        "nickname": 'Alpha #79',
        "theme": ['momentum'],
        "formula_latex": 'correlation(rank(open), rank(volume), 8) * -1',
        "columns_required": ['open', 'volume'],
        "universe": ['crypto'],
        "frequency": ['1d'],
        "min_warmup_bars": 20,
    }

def compute(panel: dict[str, pd.DataFrame]) -> pd.DataFrame:
    open_p = panel["open"]
    volume = panel["volume"]
    return -rank(open_p).rolling(8).corr(rank(volume))
