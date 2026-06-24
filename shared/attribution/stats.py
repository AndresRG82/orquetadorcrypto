import numpy as np
import pandas as pd


def compute_performance_stats(returns, risk_free_rate=0.0):
    if returns is None or len(returns) < 5:
        return {}

    returns = returns.dropna()
    if len(returns) < 5:
        return {}

    cumulative = (1 + returns).cumprod()
    total_return = cumulative.iloc[-1] - 1
    n = len(returns)
    ann_factor = 252

    avg_return = returns.mean()
    std_return = returns.std()

    excess = returns - risk_free_rate / ann_factor
    sharpe = excess.mean() / std_return * np.sqrt(ann_factor) if std_return > 0 else 0.0

    downside = returns[returns < 0]
    downside_std = downside.std() if len(downside) > 1 else std_return
    sortino = excess.mean() / downside_std * np.sqrt(ann_factor) if downside_std > 0 else 0.0

    peak = cumulative.cummax()
    drawdown = (cumulative - peak) / peak
    max_drawdown = float(drawdown.min())

    calmar = total_return / abs(max_drawdown) * ann_factor / n if max_drawdown < 0 else 0.0

    positive_trades = (returns > 0).sum()
    total_trades_att = len(returns)
    win_rate = positive_trades / total_trades_att if total_trades_att > 0 else 0.0

    avg_win = returns[returns > 0].mean() if positive_trades > 0 else 0.0
    avg_loss = returns[returns < 0].mean() if (total_trades_att - positive_trades) > 0 else 0.0
    profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    skew = float(returns.skew())
    kurt = float(returns.kurtosis())

    annualized_return = avg_return * ann_factor
    annualized_vol = std_return * np.sqrt(ann_factor)

    return {
        "total_return_pct": float(total_return * 100),
        "annualized_return_pct": float(annualized_return * 100),
        "annualized_vol_pct": float(annualized_vol * 100),
        "sharpe_ratio": float(sharpe),
        "sortino_ratio": float(sortino),
        "calmar_ratio": float(calmar),
        "max_drawdown_pct": float(max_drawdown * 100),
        "win_rate_pct": float(win_rate * 100),
        "profit_factor": float(profit_factor) if np.isfinite(profit_factor) else 999.0,
        "avg_return_pct": float(avg_return * 100),
        "avg_win_pct": float(avg_win * 100),
        "avg_loss_pct": float(avg_loss * 100),
        "skewness": float(skew),
        "kurtosis": float(kurt),
        "total_trades": total_trades_att,
        "positive_trades": int(positive_trades),
        "negative_trades": int(total_trades_att - positive_trades),
    }
