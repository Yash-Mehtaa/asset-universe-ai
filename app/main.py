"""FastAPI entry point. Boots the app, schedules the agents, exposes the API."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import router as api_router
from app.config import config
from app.db import Agent, SessionLocal, init_db
from app.agents import run_trading_cycle
from app.review import run_review

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
log = logging.getLogger("asset-universe-ai")


def _run_agent_by_name(name: str, mode: str = "trade") -> None:
    """Wrapper used by the scheduler. mode: 'trade' or 'review'."""
    db = SessionLocal()
    try:
        agent = db.query(Agent).filter_by(name=name).first()
        if not agent or not agent.is_active:
            log.warning("agent %s missing or inactive", name)
            return
        if mode == "trade":
            res = run_trading_cycle(db, agent)
            log.info("trade %s -> %s", name, res)
        elif mode == "review":
            d = run_review(db, agent, triggered_by="scheduled")
            log.info("review %s -> action=%s", name, d.action)
    except Exception:
        log.exception("scheduled run failed for %s/%s", name, mode)
    finally:
        db.close()


def _run_catalyst_scan() -> None:
    """Run the daily catalyst scan."""
    from app.catalyst import run_catalyst_scan
    db = SessionLocal()
    try:
        events = run_catalyst_scan(db)
        log.info("catalyst scan complete: %d events", len(events))
    except Exception:
        log.exception("catalyst scan failed")
    finally:
        db.close()


def _run_weekly_review_all() -> None:
    """Run weekly review for all agents and build the summary."""
    from app.review import run_review, _get_market_outlook, _build_weekly_summary
    db = SessionLocal()
    try:
        all_decisions = []
        for name in ["short_term", "mid_term", "long_term"]:
            agent = db.query(Agent).filter_by(name=name).first()
            if agent and agent.is_active:
                d = run_review(db, agent, triggered_by="weekly_scheduled")
                all_decisions.append({
                    "agent": name,
                    "action": d.action,
                    "reasoning": d.reasoning,
                    "applied_changes": d.applied_changes,
                })
                log.info("weekly review %s -> %s", name, d.action)
        outlook = _get_market_outlook(db)
        _build_weekly_summary(db, all_decisions, outlook)
        log.info("weekly review summary saved")
    except Exception:
        log.exception("weekly review all failed")
    finally:
        db.close()


def _configure_schedule(scheduler: BackgroundScheduler) -> None:
    """Wire each agent's cadence.

    Short-term: trades every 30 min during US market hours, reviews Sundays.
    Mid-term: trades daily at close (4:30pm ET), reviews 1st of month.
    Long-term: trades weekly Friday close, reviews quarterly (1st of Jan/Apr/Jul/Oct).
    """
    # NOTE: APScheduler runs in container UTC; cron values below are server-local.
    # In production we'd pin timezone='America/New_York' on each trigger.

    # Short-term — trade every 30 min, M-F, 9:30-16:00 ET (~14:30-21:00 UTC)
    scheduler.add_job(
        _run_agent_by_name, args=["short_term", "trade"],
        trigger=CronTrigger(day_of_week="mon-fri", hour="14-20", minute="0,30"),
        id="short_term_trade", replace_existing=True,
    )
    scheduler.add_job(
        _run_agent_by_name, args=["short_term", "review"],
        trigger=CronTrigger(day_of_week="sun", hour=22, minute=0),
        id="short_term_review", replace_existing=True,
    )

    # Mid-term — trade daily after close, review monthly
    scheduler.add_job(
        _run_agent_by_name, args=["mid_term", "trade"],
        trigger=CronTrigger(day_of_week="mon-fri", hour=21, minute=30),
        id="mid_term_trade", replace_existing=True,
    )
    scheduler.add_job(
        _run_agent_by_name, args=["mid_term", "review"],
        trigger=CronTrigger(day=1, hour=22, minute=0),
        id="mid_term_review", replace_existing=True,
    )

    # Long-term — rebalance Fridays, review quarterly
    scheduler.add_job(
        _run_agent_by_name, args=["long_term", "trade"],
        trigger=CronTrigger(day_of_week="fri", hour=21, minute=30),
        id="long_term_trade", replace_existing=True,
    )
    scheduler.add_job(
        _run_agent_by_name, args=["long_term", "review"],
        trigger=CronTrigger(month="1,4,7,10", day=1, hour=22, minute=0),
        id="long_term_review", replace_existing=True,
    )

    # Daily catalyst scan — runs at 6:00 AM ET (11:00 UTC) every day
    scheduler.add_job(
        _run_catalyst_scan,
        trigger=CronTrigger(hour=11, minute=0),
        id="daily_catalyst_scan", replace_existing=True,
    )

    # Weekly full review of all agents + market outlook — Sundays 10 PM ET
    scheduler.add_job(
        _run_weekly_review_all,
        trigger=CronTrigger(day_of_week="sun", hour=3, minute=0),
        id="weekly_review_all", replace_existing=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler = BackgroundScheduler()
    _configure_schedule(scheduler)
    scheduler.start()
    log.info("scheduler started with %d jobs", len(scheduler.get_jobs()))
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Asset Universe AI", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.FRONTEND_URL, "http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health")
def health():
    return {"status": "ok"}
