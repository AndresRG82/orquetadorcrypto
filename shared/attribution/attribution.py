import numpy as np
import pandas as pd
from scipy.stats import spearmanr


def compute_factor_attribution(returns, factor_returns):
    if returns is None or factor_returns is None:
        return {}

    common = returns.dropna().align(factor_returns.dropna(), join="inner")
    ret, factors = common[0], common[1]
    if len(ret) < 20 or factors.shape[1] == 0:
        return {}

    factor_names = list(factors.columns) if hasattr(factors, "columns") else [f"f{i}" for i in range(factors.shape[1])]
    n = len(ret)

    contributions = {}
    for name in factor_names:
        contrib = factors[name].dot(ret) / n
        contributions[name] = float(contrib)

    total_contrib = sum(contributions.values()) if contributions else 1.0
    pct_contributions = {}
    if abs(total_contrib) > 1e-10:
        for name, val in contributions.items():
            pct_contributions[name] = float(val / total_contrib * 100)

    specific_return = ret - factors.mean(axis=1) if factors.shape[1] > 0 else ret
    specific_risk = float(specific_return.std())
    total_risk = float(ret.std())

    explained_risk = float((ret - specific_return).std())

    return {
        "factor_contributions": contributions,
        "factor_contribution_pct": pct_contributions,
        "specific_return_vol": specific_risk,
        "total_return_vol": total_risk,
        "explained_risk": explained_risk,
        "r_squared_approximation": float(1 - specific_risk / total_risk) if total_risk > 0 else 0.0,
    }


def compute_information_coefficient(alpha_scores, forward_returns):
    if alpha_scores is None or forward_returns is None:
        return {}

    common = alpha_scores.dropna().align(forward_returns.dropna(), join="inner")
    alpha, forward = common[0], common[1]
    if len(alpha) < 5:
        return {}

    ic, pval = spearmanr(alpha, forward)
    return {
        "ic": float(ic),
        "ic_pvalue": float(pval),
        "n_observations": len(alpha),
    }


def compute_rolling_ic(alpha_scores, forward_returns, window=60):
    if alpha_scores is None or forward_returns is None:
        return {}

    common = alpha_scores.dropna().align(forward_returns.dropna(), join="inner")
    alpha, forward = common[0], common[1]
    if len(alpha) < window:
        return {}

    rolling_ics = []
    for i in range(window, len(alpha) + 1):
        ic, _ = spearmanr(alpha.iloc[i - window:i], forward.iloc[i - window:i])
        rolling_ics.append(ic)

    ic_series = pd.Series(rolling_ics, index=alpha.index[window - 1:])
    return {
        "mean_ic": float(ic_series.mean()),
        "std_ic": float(ic_series.std()),
        "ic_ratio": float(ic_series.mean() / ic_series.std()) if ic_series.std() > 0 else 0.0,
        "last_ic": float(ic_series.iloc[-1]) if len(ic_series) > 0 else 0.0,
    }
