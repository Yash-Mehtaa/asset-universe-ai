"""Configuration loaded from environment variables."""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # API Keys
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
    COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")

    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./asset_universe_ai.db")

    # Agent settings
    STARTING_CAPITAL = float(os.getenv("STARTING_CAPITAL", "10000"))

    # Frontend
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

    # Anthropic model
    CLAUDE_MODEL = "claude-opus-4-7"

    # Risk guardrails
    MAX_POSITION_PCT = 0.20          # No single position > 20% of portfolio
    MIN_CASH_FLOOR_PCT = 0.05        # Always keep 5% in cash
    MAX_DAILY_TURNOVER_PCT = 0.50    # Don't trade more than 50% of portfolio per day
    EMERGENCY_DRAWDOWN_PCT = 0.15    # Trigger off-cycle review at 15% drawdown
    MAX_PARAM_CHANGE_PCT = 0.30      # Claude can't change a parameter by more than 30%/cycle

    # Asset universe (v1)
    STOCK_UNIVERSE = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "JPM", "V", "WMT"]
    ETF_UNIVERSE = ["SPY", "QQQ", "VOO", "VTI", "IWM", "DIA"]
    CRYPTO_UNIVERSE = ["bitcoin", "ethereum", "solana"]
    COMMODITY_ETF_UNIVERSE = ["GLD", "SLV", "USO"]


config = Config()
