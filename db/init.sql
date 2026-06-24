CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS ohlcv (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume DOUBLE PRECISION
);
SELECT create_hypertable('ohlcv', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS indicators (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    rsi_14 DOUBLE PRECISION,
    macd_line DOUBLE PRECISION,
    macd_signal DOUBLE PRECISION,
    macd_hist DOUBLE PRECISION,
    bb_upper DOUBLE PRECISION,
    bb_middle DOUBLE PRECISION,
    bb_lower DOUBLE PRECISION,
    ema_9 DOUBLE PRECISION,
    ema_21 DOUBLE PRECISION,
    ema_50 DOUBLE PRECISION,
    atr_14 DOUBLE PRECISION,
    volume_sma_20 DOUBLE PRECISION
);
SELECT create_hypertable('indicators', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS signals (
    time TIMESTAMPTZ NOT NULL,
    signal_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    signal TEXT NOT NULL,
    confidence DOUBLE PRECISION,
    strategy TEXT NOT NULL,
    reasoning TEXT,
    entry_price DOUBLE PRECISION,
    target_price DOUBLE PRECISION,
    stop_loss DOUBLE PRECISION,
    approved BOOLEAN DEFAULT FALSE
);
SELECT create_hypertable('signals', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS trades (
    time TIMESTAMPTZ NOT NULL,
    order_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price DOUBLE PRECISION,
    exit_price DOUBLE PRECISION,
    quantity DOUBLE PRECISION,
    quantity_usd DOUBLE PRECISION,
    fee_usd DOUBLE PRECISION,
    pnl_usd DOUBLE PRECISION,
    status TEXT NOT NULL,
    strategy TEXT,
    confidence DOUBLE PRECISION,
    reasoning TEXT,
    stop_loss DOUBLE PRECISION,
    take_profit DOUBLE PRECISION
);
SELECT create_hypertable('trades', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    time TIMESTAMPTZ NOT NULL,
    total_value_usd DOUBLE PRECISION,
    cash_usd DOUBLE PRECISION,
    positions JSONB
);
SELECT create_hypertable('portfolio_snapshots', 'time', if_not_exists => TRUE);

CREATE TABLE IF NOT EXISTS qwen_feedback (
    time TIMESTAMPTZ NOT NULL,
    signal_id TEXT NOT NULL,
    trade_result TEXT,
    pnl DOUBLE PRECISION,
    analysis_correct BOOLEAN,
    prompt_version TEXT,
    insights TEXT
);
SELECT create_hypertable('qwen_feedback', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_tf ON ohlcv (symbol, timeframe, time DESC);
CREATE INDEX IF NOT EXISTS idx_indicators_symbol_tf ON indicators (symbol, timeframe, time DESC);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals (symbol, time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades (status, time DESC);