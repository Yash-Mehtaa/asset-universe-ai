"""Database models for the AI investing system."""
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Float, DateTime, JSON,
    ForeignKey, Text, Boolean
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

from app.config import config

Base = declarative_base()


class Agent(Base):
    """One row per AI investor. Three agents total: short, mid, long."""
    __tablename__ = "agents"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)  # "short_term", "mid_term", "long_term"
    horizon = Column(String, nullable=False)  # "short", "mid", "long"
    cash = Column(Float, nullable=False)  # Available cash
    starting_capital = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_trade_at = Column(DateTime, nullable=True)
    last_review_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)

    holdings = relationship("Holding", back_populates="agent", cascade="all, delete-orphan")
    trades = relationship("Trade", back_populates="agent", cascade="all, delete-orphan")
    strategy = relationship("Strategy", back_populates="agent", uselist=False, cascade="all, delete-orphan")
    strategy_history = relationship("StrategyHistory", back_populates="agent", cascade="all, delete-orphan")
    decisions = relationship("Decision", back_populates="agent", cascade="all, delete-orphan")
    snapshots = relationship("PerformanceSnapshot", back_populates="agent", cascade="all, delete-orphan")


class Holding(Base):
    """Current open positions per agent."""
    __tablename__ = "holdings"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    symbol = Column(String, nullable=False)
    asset_type = Column(String, nullable=False)  # "stock", "etf", "crypto", "commodity"
    quantity = Column(Float, nullable=False)
    avg_cost = Column(Float, nullable=False)  # Average cost basis
    opened_at = Column(DateTime, default=datetime.utcnow)
    last_price = Column(Float, nullable=True)
    last_price_at = Column(DateTime, nullable=True)

    agent = relationship("Agent", back_populates="holdings")


class Trade(Base):
    """Every executed trade. Append-only."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    symbol = Column(String, nullable=False)
    asset_type = Column(String, nullable=False)
    side = Column(String, nullable=False)  # "buy" or "sell"
    quantity = Column(Float, nullable=False)
    price = Column(Float, nullable=False)
    notional = Column(Float, nullable=False)  # quantity * price
    rationale = Column(Text, nullable=True)  # Why we made this trade
    strategy_snapshot = Column(JSON, nullable=True)  # Strategy params at time of trade
    executed_at = Column(DateTime, default=datetime.utcnow)

    agent = relationship("Agent", back_populates="trades")


class Strategy(Base):
    """Current active strategy for each agent. One row per agent."""
    __tablename__ = "strategies"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), unique=True, nullable=False)
    template = Column(String, nullable=False)  # "momentum", "trend_following", "risk_parity", "blended"
    params = Column(JSON, nullable=False)  # Strategy parameters as dict
    plain_english = Column(Text, nullable=True)  # User-facing explanation
    updated_at = Column(DateTime, default=datetime.utcnow)
    version = Column(Integer, default=1)

    agent = relationship("Agent", back_populates="strategy")


class StrategyHistory(Base):
    """Every strategy change ever. For both audit and self-learning."""
    __tablename__ = "strategy_history"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    template = Column(String, nullable=False)
    params = Column(JSON, nullable=False)
    changed_at = Column(DateTime, default=datetime.utcnow)
    triggered_by = Column(String, nullable=False)  # "scheduled_review", "emergency_drawdown", "initial"
    decision_id = Column(Integer, ForeignKey("decisions.id"), nullable=True)

    agent = relationship("Agent", back_populates="strategy_history")


class Decision(Base):
    """Every Claude review and its outcome."""
    __tablename__ = "decisions"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    triggered_by = Column(String, nullable=False)  # "scheduled" or "emergency"
    performance_summary = Column(JSON, nullable=False)  # Returns/sharpe/dd at decision time
    action = Column(String, nullable=False)  # "keep", "tune", "switch", "blend"
    reasoning = Column(Text, nullable=False)  # Claude's explanation
    proposed_changes = Column(JSON, nullable=True)  # What Claude wanted to change
    applied_changes = Column(JSON, nullable=True)  # What was actually applied (after validation)
    rejected_reason = Column(Text, nullable=True)  # If proposal was rejected by guardrails
    created_at = Column(DateTime, default=datetime.utcnow)

    agent = relationship("Agent", back_populates="decisions")


class PerformanceSnapshot(Base):
    """Daily snapshot of portfolio value per agent. For charts and review context."""
    __tablename__ = "performance_snapshots"

    id = Column(Integer, primary_key=True)
    agent_id = Column(Integer, ForeignKey("agents.id"), nullable=False)
    snapshot_date = Column(DateTime, default=datetime.utcnow)
    cash = Column(Float, nullable=False)
    holdings_value = Column(Float, nullable=False)
    total_value = Column(Float, nullable=False)
    pnl = Column(Float, nullable=False)  # Total P&L since inception
    pnl_pct = Column(Float, nullable=False)
    benchmark_pct = Column(Float, nullable=True)  # SPY return for comparison

    agent = relationship("Agent", back_populates="snapshots")


# Engine + session
engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in config.DATABASE_URL else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all tables. Idempotent."""
    Base.metadata.create_all(bind=engine)


def get_session():
    """Yield a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
