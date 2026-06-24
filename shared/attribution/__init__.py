from shared.attribution.stats import compute_performance_stats
from shared.attribution.beta_regression import compute_beta_regression, compute_factor_loadings
from shared.attribution.regime_analysis import classify_regime, compute_regime_stats
from shared.attribution.attribution import compute_factor_attribution


def run_full_attribution(returns, benchmark=None, factors=None, risk_free_rate=0.0):
    result = {}
    result["stats"] = compute_performance_stats(returns, risk_free_rate)
    if benchmark is not None:
        result["beta"] = compute_beta_regression(returns, benchmark, risk_free_rate)
    if factors is not None:
        result["factor_loadings"] = compute_factor_loadings(returns, factors, risk_free_rate)
        result["factor_attribution"] = compute_factor_attribution(returns, factors)
    result["regime"] = classify_regime(returns)
    return result
