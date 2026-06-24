import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_URL: str = f"redis://{REDIS_HOST}:{REDIS_PORT}"

    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", "5432"))
    DB_NAME: str = os.getenv("DB_NAME", "trader")
    DB_USER: str = os.getenv("DB_USER", "trader")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "trader123")
    DB_URL: str = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

    OLLAMA_HOST: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gemma3:4b")
    OLLAMA_FALLBACK_MODELS: list = ["gemma3:4b", "qwen2.5:3b"]

    EXCHANGE: str = os.getenv("EXCHANGE", "binance")

    INITIAL_CAPITAL: float = float(os.getenv("INITIAL_CAPITAL", "1000.0"))
    BASE_CURRENCY: str = os.getenv("BASE_CURRENCY", "USDT")

    MAX_POSITION_PCT: float = float(os.getenv("MAX_POSITION_PCT", "0.20"))
    MAX_DRAWDOWN_PCT: float = float(os.getenv("MAX_DRAWDOWN_PCT", "0.10"))

    SLIPPAGE_PCT: float = float(os.getenv("SLIPPAGE_PCT", "0.001"))
    TRADING_FEE_PCT: float = float(os.getenv("TRADING_FEE_PCT", "0.00075"))

    QWEN_ANALYZER_URL: str = os.getenv("QWEN_ANALYZER_URL", "http://localhost:8000")

    STREAM_MARKET_DATA: str = "market:data"
    STREAM_INDICATORS: str = "market:indicators"
    STREAM_SIGNALS: str = "strategy:signals"
    STREAM_RISK_APPROVED: str = "risk:approved"
    STREAM_TRADE_ORDERS: str = "trade:orders"
    STREAM_TRADE_RESULTS: str = "trade:results"
    STREAM_SENTIMENT: str = "sentiment:updates"

    TOP_PAIRS: list[str] = [
        "BTC/USDT", "ETH/USDT", "BNB/USDT", "XRP/USDT", "SOL/USDT",
        "ADA/USDT", "DOGE/USDT", "AVAX/USDT", "DOT/USDT", "LINK/USDT",
        "LTC/USDT", "ATOM/USDT", "UNI/USDT", "NEAR/USDT", "APT/USDT",
        "ARB/USDT", "OP/USDT", "FIL/USDT", "SUI/USDT", "PEPE/USDT",
    ]

    TIMEFRAMES: list[str] = ["1m", "5m", "15m", "1h", "4h", "1d", "1w"]

    SCAN_INTERVALS: dict[str, int] = {
        "1m": 30, "5m": 120, "15m": 300,
        "1h": 900, "4h": 1800, "1d": 14400, "1w": 43200,
    }

    CANDLE_LIMITS: dict[str, int] = {
        "1m": 200, "5m": 200, "15m": 200,
        "1h": 200, "4h": 200, "1d": 200, "1w": 100,
    }


settings = Settings()