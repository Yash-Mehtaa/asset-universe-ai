"""Daily catalyst scan: Claude with web search finds top 5 upcoming market events."""
from __future__ import annotations

import json
import re
from datetime import datetime

from anthropic import Anthropic
from sqlalchemy.orm import Session

from app.config import config
from app.db import CatalystEvent, SessionLocal
from app.budget import log_usage, check_budget

_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)

CATALYST_PROMPT = """Search the web right now for upcoming market-moving events in the next 7 days.

Find the top 5 most important upcoming catalysts that could significantly move stock prices. Focus on:
- Earnings reports from major companies
- Federal Reserve meetings or speeches
- Major economic data releases (CPI, jobs report, GDP, etc.)
- Geopolitical events with market impact
- Sector-specific regulatory decisions

Respond with ONLY a JSON array, no markdown, no explanation:
[
  {
    "rank": 1,
    "symbol": "NVDA",
    "event_type": "earnings",
    "title": "NVIDIA Q1 2025 Earnings Report",
    "description": "2-3 sentences on what to expect and why it matters for markets.",
    "expected_impact": "bullish",
    "date_of_event": "2025-05-28"
  }
]

Rules:
- rank 1 is the most important
- symbol can be null for macro events like Fed meetings
- event_type: earnings, fed_meeting, economic_data, geopolitical, regulatory, other
- expected_impact: bullish, bearish, or neutral
- date_of_event: specific date if known, otherwise "this week" or "next week"
- description must reference actual specific details you found via web search"""


def run_catalyst_scan(db: Session | None = None) -> list[dict]:
    """Run the daily catalyst scan. Returns list of events saved."""
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        ok, spend, cap = check_budget(db)
        if not ok:
            print(f"[catalyst] Budget cap reached (${spend:.2f}/${cap:.2f}), skipping scan")
            return []

        response = _client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=1500,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": CATALYST_PROMPT}],
        )

        # Log usage
        log_usage(
            db,
            endpoint="catalyst_scan",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

        # Extract text from response
        text = "\n".join(
            b.text for b in response.content
            if hasattr(b, "text") and b.text is not None
        )
        text = re.sub(r"```json|```", "", text).strip()

        match = re.search(r'\[.*\]', text, re.DOTALL)
        if not match:
            print(f"[catalyst] Could not parse response: {text[:200]}")
            return []

        events = json.loads(match.group())

        # Delete today's existing scan if any
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        db.query(CatalystEvent).filter(CatalystEvent.scan_date >= today_start).delete()

        saved = []
        for e in events[:5]:
            row = CatalystEvent(
                scan_date=datetime.utcnow(),
                rank=e.get("rank", 0),
                symbol=e.get("symbol"),
                event_type=e.get("event_type", "other"),
                title=e.get("title", ""),
                description=e.get("description", ""),
                expected_impact=e.get("expected_impact", "neutral"),
                date_of_event=e.get("date_of_event"),
            )
            db.add(row)
            saved.append(e)

        db.commit()
        print(f"[catalyst] Saved {len(saved)} events")
        return saved

    except Exception as e:
        print(f"[catalyst] Error: {e}")
        return []
    finally:
        if close_db:
            db.close()
