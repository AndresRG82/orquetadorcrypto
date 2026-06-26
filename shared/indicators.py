import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange


def compute_indicators(df: pd.DataFrame) -> dict:
    result = {}
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    try:
        rsi = RSIIndicator(close, window=14)
        result["rsi_14"] = float(rsi.rsi().iloc[-1]) if not rsi.rsi().iloc[-1] != rsi.rsi().iloc[-1] else None
    except Exception:
        result["rsi_14"] = None

    try:
        macd = MACD(close)
        macd_line = macd.macd().iloc[-1]
        macd_signal = macd.macd_signal().iloc[-1]
        macd_hist = macd.macd_diff().iloc[-1]
        result["macd_line"] = float(macd_line) if macd_line == macd_line else None
        result["macd_signal"] = float(macd_signal) if macd_signal == macd_signal else None
        result["macd_hist"] = float(macd_hist) if macd_hist == macd_hist else None
    except Exception:
        result["macd_line"] = result["macd_signal"] = result["macd_hist"] = None

    try:
        bb = BollingerBands(close)
        result["bb_upper"] = float(bb.bollinger_hband().iloc[-1]) if bb.bollinger_hband().iloc[-1] == bb.bollinger_hband().iloc[-1] else None
        result["bb_middle"] = float(bb.bollinger_mavg().iloc[-1]) if bb.bollinger_mavg().iloc[-1] == bb.bollinger_mavg().iloc[-1] else None
        result["bb_lower"] = float(bb.bollinger_lband().iloc[-1]) if bb.bollinger_lband().iloc[-1] == bb.bollinger_lband().iloc[-1] else None
    except Exception:
        result["bb_upper"] = result["bb_middle"] = result["bb_lower"] = None

    for window, key in [(9, "ema_9"), (21, "ema_21"), (50, "ema_50")]:
        try:
            ema = EMAIndicator(close, window=window)
            val = ema.ema_indicator().iloc[-1]
            result[key] = float(val) if val == val else None
        except Exception:
            result[key] = None

    try:
        atr = AverageTrueRange(high, low, close, window=14)
        result["atr_14"] = float(atr.average_true_range().iloc[-1]) if atr.average_true_range().iloc[-1] == atr.average_true_range().iloc[-1] else None
    except Exception:
        result["atr_14"] = None

    try:
        result["volume_sma_20"] = float(volume.rolling(20).mean().iloc[-1]) if volume.rolling(20).mean().iloc[-1] == volume.rolling(20).mean().iloc[-1] else None
    except Exception:
        result["volume_sma_20"] = None

    result["price_change_pct"] = float((close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100) if len(close) >= 2 else None
    result["volume_change_pct"] = float((volume.iloc[-1] - volume.iloc[-2]) / volume.iloc[-2] * 100) if len(close) >= 2 and volume.iloc[-2] > 0 else None

    return result
