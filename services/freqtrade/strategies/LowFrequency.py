from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter, CategoricalParameter
from freqtrade.persistence import Trade
from pandas import DataFrame
import talib
import numpy as np


class LowFrequency(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = '5m'
    can_short = False

    minimal_roi = {
        "0": 0.035,
        "60": 0.025,
        "120": 0.015,
        "480": 0,
    }

    stoploss = -0.02

    trailing_stop = True
    trailing_stop_positive = 0.01
    trailing_stop_positive_offset = 0.02
    trailing_only_offset_is_reached = True

    max_open_trades = 1
    startup_candle_count = 100

    buy_rsi_oversold = IntParameter(20, 30, default=25, space='buy')
    buy_bb_position_low = DecimalParameter(0.08, 0.2, default=0.12, space='buy')
    buy_min_score = IntParameter(4, 6, default=5, space='buy')

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

        return dataframe

    def populate_buy_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        bb_low = self.buy_bb_position_low.value
        min_score = self.buy_min_score.value

        dataframe['score'] = 0
        dataframe.loc[dataframe['rsi'] < self.buy_rsi_oversold.value, 'score'] += 2
        dataframe.loc[(dataframe['macd'] > dataframe['macdsignal']) & (dataframe['macdhist'] > 0), 'score'] += 1
        dataframe.loc[dataframe['bb_pos'] < bb_low, 'score'] += 1
        dataframe.loc[(dataframe['bb_pos'] < bb_low) & (dataframe['bb_squeeze'] == 1), 'score'] += 1
        dataframe.loc[dataframe['ema_9'] > dataframe['ema_21'], 'score'] += 1

        if self.use_volume_confirmation:
            dataframe.loc[(dataframe['volume_spike'] == 1) & (dataframe['score'] > 0), 'score'] += 1

        dataframe.loc[(dataframe['score'] >= min_score) & (dataframe['volume'] > 0), 'buy'] = 1

        return dataframe

    def populate_sell_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        sell_score = 0
        sell_score += (dataframe['rsi'] > 75).astype(int) * 2
        sell_score += ((dataframe['macd'] < dataframe['macdsignal']) & (dataframe['macdhist'] < 0)).astype(int)
        sell_score += (dataframe['bb_pos'] > 0.85).astype(int)
        sell_score += (dataframe['ema_9'] < dataframe['ema_21']).astype(int)

        dataframe.loc[(sell_score >= 3) & (dataframe['volume'] > 0), 'sell'] = 1

        return dataframe

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float, rate: float,
                            time_in_force: str, current_time, entry_tag, side: str,
                            **kwargs) -> bool:
        trades_today = Trade.get_trades_proxy(
            pair=pair,
            is_open=False,
        )
        trades_today = [t for t in trades_today if t.open_date_utc.date() == current_time.date()]
        if len(trades_today) >= 1:
            return False
        return True
