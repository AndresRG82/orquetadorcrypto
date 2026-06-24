import numpy as np
import pandas as pd


def classify_regime(prices_or_returns, sma_fast=20, sma_slow=50, vol_window=21):
    if prices_or_returns is None or len(prices_or_returns) < sma_slow:
        return {}

    if prices_or_returns.min() < -0.5 or prices_or_returns.max() < 10:
        prices = (1 + prices_or_returns.dropna()).cumprod()
        prices.iloc[0] = 1.0
    else:
        prices = prices_or_returns.dropna()

    if len(prices) < sma_slow:
        return {}

    sma_f = prices.rolling(sma_fast).mean()
    sma_s = prices.rolling(sma_slow).mean()

    trend = pd.Series("unknown", index=sma_s.index)
    trend[sma_f > sma_s * 1.005] = "bull"
    trend[sma_f < sma_s * 0.995] = "bear"
    trend[(sma_f >= sma_s * 0.995) & (sma_f <= sma_s * 1.005)] = "sideways"

    current_trend = trend.iloc[-1] if len(trend) > 0 else "unknown"

    bull_pct = (trend == "bull").sum() / len(trend) * 100 if len(trend) > 0 else 0
    bear_pct = (trend == "bear").sum() / len(trend) * 100 if len(trend) > 0 else 0
    sideways_pct = (trend == "sideways").sum() / len(trend) * 100 if len(trend) > 0 else 0

    returns = prices.pct_change().dropna()
    rolling_vol = returns.rolling(vol_window).std() * np.sqrt(252)
    current_vol = rolling_vol.iloc[-1] if len(rolling_vol) > 0 else 0.0
    median_vol = rolling_vol.median() if len(rolling_vol) > 0 else current_vol

    if median_vol > 0:
        vol_ratio = current_vol / median_vol
    else:
        vol_ratio = 1.0

    if vol_ratio < 0.7:
        vol_regime = "low"
    elif vol_ratio < 1.3:
        vol_regime = "normal"
    else:
        vol_regime = "high"

    sma_distance = (sma_f.iloc[-1] - sma_s.iloc[-1]) / sma_s.iloc[-1] if sma_s.iloc[-1] > 0 else 0

    return {
        "current_trend": current_trend,
        "trend_strength_pct": float(abs(sma_distance) * 100),
        "sma_distance_pct": float(sma_distance * 100),
        "bull_pct": float(bull_pct),
        "bear_pct": float(bear_pct),
        "sideways_pct": float(sideways_pct),
        "current_volatility_pct": float(current_vol * 100),
        "median_volatility_pct": float(median_vol * 100),
        "vol_regime": vol_regime,
        "vol_ratio": float(vol_ratio),
    }


def compute_regime_stats(returns, regime_classifications):
    if returns is None or regime_classifications is None:
        return {}

    aligned = returns.dropna().align(regime_classifications.dropna(), join="inner")
    if len(aligned[0]) < 5:
        return {}

    ret, regimes = aligned[0], aligned[1]
    unique_regimes = regimes.unique()

    stats = {}
    for regime in unique_regimes:
        mask = regimes == regime
        regime_returns = ret[mask]
        if len(regime_returns) < 3:
            continue
        ann_factor = 252
        avg_ret = regime_returns.mean() * ann_factor * 100
        vol = regime_returns.std() * np.sqrt(ann_factor) * 100
        sharpe = avg_ret / vol if vol > 0 else 0
        stats[str(regime)] = {
            "n_observations": int(mask.sum()),
            "avg_return_pct": float(avg_ret),
            "volatility_pct": float(vol),
            "sharpe": float(sharpe),
        }

    return stats
