import numpy as np
import pandas as pd
from enum import Enum


class Market(Enum):
    EQUITY_US = "equity_us"
    EQUITY_CN = "equity_cn"
    EQUITY_HK = "equity_hk"
    CRYPTO = "crypto"
    FUTURES = "futures"


def safe_div(a: pd.DataFrame | pd.Series, b: pd.DataFrame | pd.Series | float) -> pd.DataFrame | pd.Series:
    out = a / b.replace(0, np.nan)
    return out.replace([np.inf, -np.inf], np.nan)


def signed_power(x: pd.DataFrame | pd.Series, p: float) -> pd.DataFrame | pd.Series:
    return x.abs() ** p * np.sign(x)


def rank(df: pd.DataFrame) -> pd.DataFrame:
    return df.rank(axis=1, pct=True, na_option="keep")


def scale(df: pd.DataFrame) -> pd.DataFrame:
    row_sum = df.abs().sum(axis=1, skipna=True).replace(0, np.nan)
    return df.div(row_sum, axis=0)


def delta(df: pd.DataFrame, d: int) -> pd.DataFrame:
    if d < 1:
        raise ValueError(f"delta requires d >= 1, got {d}")
    return df.diff(d)


def decay_linear(df: pd.DataFrame, d: int) -> pd.DataFrame:
    weights = np.arange(1, d + 1, dtype=float)
    weights /= weights.sum()
    return df.rolling(d, min_periods=d).apply(
        lambda x: np.dot(x, weights), raw=True
    )


def ts_mean(df: pd.DataFrame, window: int) -> pd.DataFrame:
    return df.rolling(window, min_periods=window).mean()


def ts_std(df: pd.DataFrame, window: int) -> pd.DataFrame:
    return df.rolling(window, min_periods=window).std(ddof=1)


def ts_max(df: pd.DataFrame, window: int) -> pd.DataFrame:
    return df.rolling(window, min_periods=window).max()


def ts_min(df: pd.DataFrame, window: int) -> pd.DataFrame:
    return df.rolling(window, min_periods=window).min()


def ts_argmax(df: pd.DataFrame, window: int) -> pd.DataFrame:
    return df.rolling(window, min_periods=window).apply(
        lambda x: x.idxmax() if len(x.dropna()) == window else np.nan,
        raw=True,
    )


def ts_argmin(df: pd.DataFrame, window: int) -> pd.DataFrame:
    return df.rolling(window, min_periods=window).apply(
        lambda x: x.idxmin() if len(x.dropna()) == window else np.nan,
        raw=True,
    )


def ts_rank(df: pd.DataFrame, window: int) -> pd.DataFrame:
    return df.rolling(window, min_periods=window).rank(pct=True)


def ts_corr(df_a: pd.DataFrame, df_b: pd.DataFrame, window: int) -> pd.DataFrame:
    return df_a.rolling(window, min_periods=window).corr(df_b)


def ts_cov(df_a: pd.DataFrame, df_b: pd.DataFrame, window: int) -> pd.DataFrame:
    return df_a.rolling(window, min_periods=window).cov(df_b)


def vwap(panel: dict[str, pd.DataFrame], market: Market) -> pd.DataFrame:
    close = panel.get("close")
    volume = panel.get("volume")
    if market == Market.CRYPTO and "vwap" in panel and panel["vwap"] is not None:
        return panel["vwap"]
    if market == Market.EQUITY_CN:
        amount = panel.get("amount")
        if amount is not None and volume is not None:
            return (amount * 1000) / (volume * 100 + 1).replace(0, np.nan)
    if close is not None and volume is not None:
        high = panel.get("high", close)
        low = panel.get("low", close)
        typical = (high + low + close) / 3
        return (typical * volume).rolling(20).sum() / volume.rolling(20).sum().replace(0, np.nan)
    return close
