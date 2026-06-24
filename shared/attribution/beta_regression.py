import numpy as np
import pandas as pd
from scipy.stats import t


def _ols_regression(y, X):
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)

    X = np.column_stack([np.ones(X.shape[0]), X])

    try:
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        residuals = y - X @ beta
        n, k = X.shape
        mse = np.sum(residuals ** 2) / (n - k)
        var_beta = mse * np.linalg.inv(X.T @ X)
        se = np.sqrt(np.diag(var_beta))
        t_stats = beta / se
        p_values = t.sf(np.abs(t_stats), n - k) * 2
        r_squared = 1 - np.sum(residuals ** 2) / np.sum((y - y.mean()) ** 2)
        adj_r_squared = 1 - (1 - r_squared) * (n - 1) / (n - k)
    except np.linalg.LinAlgError:
        return {
            "alpha": 0.0, "alpha_se": 0.0, "alpha_tstat": 0.0, "alpha_pvalue": 1.0,
            "beta": 0.0, "beta_se": 0.0, "beta_tstat": 0.0, "beta_pvalue": 1.0,
            "r_squared": 0.0, "adj_r_squared": 0.0, "residual_vol": 0.0,
        }

    return {
        "alpha": float(beta[0]),
        "alpha_se": float(se[0]),
        "alpha_tstat": float(t_stats[0]),
        "alpha_pvalue": float(p_values[0]),
        "beta": float(beta[1]) if len(beta) > 1 else 0.0,
        "beta_se": float(se[1]) if len(se) > 1 else 0.0,
        "beta_tstat": float(t_stats[1]) if len(t_stats) > 1 else 0.0,
        "beta_pvalue": float(p_values[1]) if len(p_values) > 1 else 1.0,
        "r_squared": float(r_squared),
        "adj_r_squared": float(adj_r_squared),
        "residual_vol": float(np.std(residuals, ddof=k)),
    }


def compute_beta_regression(returns, benchmark_returns, risk_free_rate=0.0):
    if returns is None or benchmark_returns is None:
        return {}

    common = returns.dropna().align(benchmark_returns.dropna(), join="inner")
    y, bm = common[0], common[1]
    if len(y) < 10:
        return {}

    ann_factor = 252
    rf_daily = risk_free_rate / ann_factor
    excess_returns = y - rf_daily
    excess_benchmark = bm - rf_daily

    result = _ols_regression(excess_returns, excess_benchmark)
    result["benchmark_beta"] = result.pop("beta", 0.0)
    result["jensen_alpha"] = result.pop("alpha", 0.0)
    result["jensen_alpha_annualized"] = result["jensen_alpha"] * ann_factor
    return result


def compute_factor_loadings(returns, factor_returns, risk_free_rate=0.0):
    if returns is None or factor_returns is None:
        return {}

    common = returns.dropna().align(factor_returns.dropna(), join="inner")
    y, factors = common[0], common[1]
    if len(y) < 20:
        return {}

    ann_factor = 252
    rf_daily = risk_free_rate / ann_factor
    y_excess = y - rf_daily

    factor_names = list(factors.columns) if hasattr(factors, "columns") else [f"f{i}" for i in range(factors.shape[1])]
    X = factors.values
    y_arr = y_excess.values

    X_with_const = np.column_stack([np.ones(X.shape[0]), X])
    try:
        beta = np.linalg.lstsq(X_with_const, y_arr, rcond=None)[0]
        residuals = y_arr - X_with_const @ beta
        r_squared = 1 - np.sum(residuals ** 2) / np.sum((y_arr - y_arr.mean()) ** 2)
        n, k = X_with_const.shape
        adj_r_squared = 1 - (1 - r_squared) * (n - 1) / (n - k)
        model_intercept = beta[0]
        model_coefs = beta[1:]
    except np.linalg.LinAlgError:
        return {"error": "regression failed"}

    annualized_intercept = model_intercept * ann_factor

    return {
        "alpha_annualized": float(annualized_intercept),
        "r_squared": float(r_squared),
        "adj_r_squared": float(adj_r_squared),
        "residual_vol": float(np.std(residuals, ddof=k)),
        "factor_loadings": {name: float(coef) for name, coef in zip(factor_names, np.atleast_1d(model_coefs))},
        "n_observations": n,
    }
