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

    # Anthropic model — Sonnet for cost efficiency
    CLAUDE_MODEL = "claude-sonnet-4-5"

    # Budget cap — total Claude spend per month in USD
    MONTHLY_BUDGET_CAP_USD = 10.00
    # Sonnet pricing (per million tokens, as of 2025)
    COST_PER_INPUT_TOKEN = 3.00 / 1_000_000
    COST_PER_OUTPUT_TOKEN = 15.00 / 1_000_000

    # Risk guardrails
    MAX_POSITION_PCT = 0.30
    MIN_CASH_FLOOR_PCT = 0.05
    MAX_DAILY_TURNOVER_PCT = 0.60
    EMERGENCY_DRAWDOWN_PCT = 0.15
    MAX_PARAM_CHANGE_PCT = 0.30

    # Mandatory review change: force non-keep if strategy unchanged this many days
    MANDATORY_CHANGE_DAYS = 30

    # Asset universe
    STOCK_UNIVERSE = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "JPM", "V", "WMT"]
    ETF_UNIVERSE = ["SPY", "QQQ", "VOO", "VTI", "IWM", "DIA"]
    CRYPTO_UNIVERSE = ["bitcoin", "ethereum", "solana"]
    COMMODITY_ETF_UNIVERSE = ["GLD", "SLV", "USO"]


config = Config()
