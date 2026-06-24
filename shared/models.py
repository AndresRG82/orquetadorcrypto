from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from enum import Enum


class SignalType(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class OrderStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class OHLCVData(BaseModel):
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class TechnicalIndicators(BaseModel):
    symbol: str
    timeframe: str
    timestamp: datetime
    close: float
    rsi_14: Optional[float] = None
    macd_line: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    ema_9: Optional[float] = None
    ema_21: Optional[float] = None
    ema_50: Optional[float] = None
    atr_14: Optional[float] = None
    volume_sma_20: Optional[float] = None
    price_change_pct: Optional[float] = None
    volume_change_pct: Optional[float] = None


class TradingSignal(BaseModel):
    signal_id: str
    symbol: str
    timeframe: str
    timestamp: datetime
    signal: SignalType
    confidence: float = Field(ge=0.0, le=1.0)
    strategy: str
    reasoning: str
    entry_price: float
    target_price: Optional[float] = None
    stop_loss: Optional[float] = None
    indicators_snapshot: Optional[TechnicalIndicators] = None


class RiskAssessment(BaseModel):
    signal_id: str
    approved: bool
    reason: str
    position_size_usd: Optional[float] = None
    adjusted_stop_loss: Optional[float] = None
    risk_reward_ratio: Optional[float] = None
    portfolio_risk_pct: Optional[float] = None


class TradeOrder(BaseModel):
    order_id: str
    signal_id: str
    symbol: str
    side: SignalType
    entry_price: float
    quantity_usd: float
    quantity: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    strategy: str
    confidence: float
    reasoning: str
    timestamp: datetime


class TradeResult(BaseModel):
    order_id: str
    symbol: str
    side: SignalType
    entry_price: float
    exit_price: float
    quantity: float
    quantity_usd: float
    fee_usd: float
    slippage_usd: float
    pnl_usd: float
    status: OrderStatus
    strategy: str
    confidence: float
    reasoning: str
    timestamp: datetime
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


class Position(BaseModel):
    symbol: str
    side: SignalType
    quantity: float
    entry_price: float
    quantity_usd: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    strategy: str
    order_id: str
    opened_at: datetime


class PortfolioSnapshot(BaseModel):
    timestamp: datetime
    total_value_usd: float
    cash_usd: float
    positions: list[Position]
    unrealized_pnl_usd: float = 0.0


class QwenFeedback(BaseModel):
    signal_id: str
    trade_result: Optional[str] = None
    pnl: Optional[float] = None
    analysis_correct: Optional[bool] = None
    prompt_version: str
    insights: Optional[str] = None