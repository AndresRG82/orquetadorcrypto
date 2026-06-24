import talib
import numpy as np
from pandas import DataFrame
from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter


class MeanReversion(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = '5m'
    can_short = False

    minimal_roi = {
        "0": 0.03,
        "30": 0.025,
        "60": 0.02,
        "120": 0.015,
        "240": 0,
    }

    stoploss = -0.025

    trailing_stop = True
    trailing_stop_positive = 0.008
    trailing_stop_positive_offset = 0.015
    trailing_only_offset_is_reached = True

    max_open_trades = 3
    startup_candle_count = 100

    buy_rsi_oversold_strong = IntParameter(20, 30, default=25, space='buy')
    buy_rsi_oversold_weak = IntParameter(30, 40, default=35, space='buy')
    buy_bb_position_low = DecimalParameter(0.1, 0.25, default=0.15, space='buy')
    buy_min_score = IntParameter(3, 5, default=4, space='buy')

    sell_rsi_overbought_strong = IntParameter(70, 80, default=75, space='sell')
    sell_bb_position_high = DecimalParameter(0.75, 0.9, default=0.85, space='sell')

    use_volume_confirmation = True

    def informative_pairs(self):
        pairs = self.config['exchange']['pair_whitelist']
        return list(set([("BTC/USDT", "1h")] + [(p, "1h") for p in pairs]))

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
        bb_width = np.where(
            dataframe['bb_middle'] > 0,
            bb_range / dataframe['bb_middle'],
            0
        )
        dataframe['bb_squeeze'] = (bb_width < 0.02).astype(int)

        dataframe['macd'], dataframe['macdsignal'], dataframe['macdhist'] = talib.MACD(
            dataframe['close'], fastperiod=12, slowperiod=26, signalperiod=9
        )

        dataframe['ema_9'] = talib.EMA(dataframe['close'], timeperiod=9)
        dataframe['ema_21'] = talib.EMA(dataframe['close'], timeperiod=21)

        dataframe['volume_sma_20'] = dataframe['volume'].rolling(20).mean()
        dataframe['volume_spike'] = (dataframe['volume'] > dataframe['volume_sma_20'] * 1.5).astype(int)

        dataframe['atr'] = talib.ATR(dataframe['high'], dataframe['low'], dataframe['close'], timeperiod=14)
        dataframe['atr_pct'] = dataframe['atr'] / dataframe['close'] * 100

        return dataframe

    def populate_buy_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        p = self.buy_rsi_oversold_strong.value
        p_w = self.buy_rsi_oversold_weak.value
        bb_low = self.buy_bb_position_low.value
        min_score = self.buy_min_score.value

        dataframe['score'] = 0
        dataframe.loc[dataframe['rsi'] < p, 'score'] += 2
        dataframe.loc[(dataframe['rsi'] >= p) & (dataframe['rsi'] < p_w), 'score'] += 1
        dataframe.loc[(dataframe['macd'] > dataframe['macdsignal']) & (dataframe['macdhist'] > 0), 'score'] += 1
        dataframe.loc[dataframe['bb_pos'] < bb_low, 'score'] += 1
        dataframe.loc[(dataframe['bb_pos'] < bb_low) & (dataframe['bb_squeeze'] == 1), 'score'] += 1
        dataframe.loc[dataframe['ema_9'] > dataframe['ema_21'], 'score'] += 1

        if self.use_volume_confirmation:
            dataframe.loc[(dataframe['volume_spike'] == 1) & (dataframe['score'] > 0), 'score'] += 1

        dataframe.loc[(dataframe['score'] >= min_score) & (dataframe['volume'] > 0), 'buy'] = 1

        return dataframe

    def populate_sell_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        p = self.sell_rsi_overbought_strong.value
        bb_high = self.sell_bb_position_high.value

        sell_score = 0
        sell_score += (dataframe['rsi'] > p).astype(int) * 2
        sell_score += ((dataframe['macd'] < dataframe['macdsignal']) & (dataframe['macdhist'] < 0)).astype(int)
        sell_score += (dataframe['bb_pos'] > bb_high).astype(int)
        sell_score += (dataframe['ema_9'] < dataframe['ema_21']).astype(int)

        dataframe.loc[(sell_score >= 3) & (dataframe['volume'] > 0), 'sell'] = 1

        return dataframe
