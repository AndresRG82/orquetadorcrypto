import talib
import numpy as np
from pandas import DataFrame
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter


class SwingStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = '1h'
    can_short = False

    minimal_roi = {
        "0": 0.05,
        "60": 0.04,
        "120": 0.03,
        "480": 0.02,
        "1440": 0,
    }

    stoploss = -0.03

    trailing_stop = True
    trailing_stop_positive = 0.015
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    max_open_trades = 2
    startup_candle_count = 100

    buy_rsi_oversold = IntParameter(25, 40, default=30, space='buy')
    buy_min_score_strong = IntParameter(4, 6, default=5, space='buy')

    use_volume_confirmation = True

    def informative_pairs(self):
        pairs = self.config['exchange']['pair_whitelist']
        return list(set([("BTC/USDT", "4h")] + [(p, "4h") for p in pairs]))

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe['rsi'] = talib.RSI(dataframe['close'], timeperiod=14)

        dataframe['bb_upper'], dataframe['bb_middle'], dataframe['bb_lower'] = talib.BBANDS(
            dataframe['close'], timeperiod=20, nbdevup=2, nbdevdn=2
        )
        bb_range = dataframe['bb_upper'] - dataframe['bb_lower']
        dataframe['bb_pos'] = np.where(
            bb_range > 0,
            (dataframe['close'] - dataframe['bb_lower']) / bb_range,
            0.5
        )

        dataframe['macd'], dataframe['macdsignal'], dataframe['macdhist'] = talib.MACD(
            dataframe['close'], fastperiod=12, slowperiod=26, signalperiod=9
        )

        dataframe['ema_9'] = talib.EMA(dataframe['close'], timeperiod=9)
        dataframe['ema_21'] = talib.EMA(dataframe['close'], timeperiod=21)
        dataframe['ema_50'] = talib.EMA(dataframe['close'], timeperiod=50)

        dataframe['volume_sma_20'] = dataframe['volume'].rolling(20).mean()
        dataframe['volume_surge'] = (dataframe['volume'] > dataframe['volume_sma_20'] * 1.8).astype(int)

        dataframe['atr'] = talib.ATR(dataframe['high'], dataframe['low'], dataframe['close'], timeperiod=14)
        dataframe['atr_pct'] = dataframe['atr'] / dataframe['close'] * 100

        return dataframe

    def populate_buy_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        min_score = self.buy_min_score_strong.value

        dataframe['score'] = 0
        dataframe.loc[dataframe['rsi'] < self.buy_rsi_oversold.value, 'score'] += 2
        dataframe.loc[(dataframe['macd'] > dataframe['macdsignal']) & (dataframe['macdhist'] > 0), 'score'] += 2
        dataframe.loc[dataframe['ema_9'] > dataframe['ema_21'], 'score'] += 1
        dataframe.loc[(dataframe['ema_9'] > dataframe['ema_21']) & (dataframe['ema_21'] > dataframe['ema_50']), 'score'] += 2
        dataframe.loc[dataframe['bb_pos'] < 0.1, 'score'] += 1

        if self.use_volume_confirmation:
            dataframe.loc[(dataframe['volume_surge'] == 1) & (dataframe['score'] > 0), 'score'] += 1

        dataframe.loc[(dataframe['score'] >= min_score) & (dataframe['volume'] > 0), 'buy'] = 1

        return dataframe

    def populate_sell_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        sell_score = 0
        sell_score += (dataframe['rsi'] > 70).astype(int) * 2
        sell_score += ((dataframe['macd'] < dataframe['macdsignal']) & (dataframe['macdhist'] < 0)).astype(int) * 2
        sell_score += (dataframe['bb_pos'] > 0.90).astype(int)
        sell_score += (dataframe['ema_9'] < dataframe['ema_21']).astype(int)
        sell_score += ((dataframe['ema_9'] < dataframe['ema_21']) & (dataframe['ema_21'] < dataframe['ema_50'])).astype(int) * 2

        dataframe.loc[(sell_score >= 4) & (dataframe['volume'] > 0), 'sell'] = 1

        return dataframe

    def custom_stoploss(self, pair: str, trade, current_time, current_rate, current_profit, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is not None and len(dataframe) > 0:
            last_candle = dataframe.iloc[-1]
            atr_pct = last_candle.get('atr_pct', 2.0)
            dynamic_sl = max(-atr_pct * 1.5 / 100, -0.05)
            if current_profit > 0.02:
                return min(dynamic_sl, current_profit - 0.01)
            return dynamic_sl
        return self.stoploss
