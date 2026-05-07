"""AI reasoning module. Uses Claude with web search to generate
plain-English explanations for every trade decision, including
real news context with timestamps."""
from __future__ import annotations

from anthropic import Anthropic
from app.config import config

_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)


def generate_trade_reasoning(trade) -> str:
    """Generate Claude's plain-English reasoning for a trade, with news context."""
    side = trade.side.upper()
    symbol = trade.symbol
    price = trade.price
    notional = trade.notional
    rationale = trade.rationale or ""
    realized_pnl = trade.realized_pnl

    if side == "BUY":
        prompt = f"""You are an AI investment agent. You just bought {symbol} at ${price:.2f} (${notional:.0f} total).
Your strategy signal was: "{rationale}"

Search for the very latest news about {symbol} right now. Find the most recent headline with its date and time.

Then write your reasoning in this exact format:

My decision: I bought {symbol} at ${price:.2f}

My reason: [2-3 sentences explaining WHY you bought this stock right now, combining your momentum signal with what you found in the news. Be specific about the price movement and what's driving it.]

Latest news: "[Exact headline you found]" — [Source] · [Date and time]

What I expect: [1 sentence on why you think this could be profitable short-term]"""

    else:
        pnl_str = f"+${realized_pnl:.2f}" if realized_pnl and realized_pnl > 0 else f"-${abs(realized_pnl):.2f}" if realized_pnl else "$0.00"
        prompt = f"""You are an AI investment agent. You just sold {symbol} at ${price:.2f} (${notional:.0f} total). Realized P&L: {pnl_str}.
Your strategy signal was: "{rationale}"

Search for the very latest news about {symbol} right now. Find the most recent headline with its date and time.

Then write your reasoning in this exact format:

My decision: I sold {symbol} at ${price:.2f} — Realized {pnl_str}

My reason: [2-3 sentences explaining WHY you sold this stock right now, combining your signal with what you found in the news.]

Latest news: "[Exact headline you found]" — [Source] · [Date and time]

What I learned: [1 sentence on what this trade taught you about the market right now]"""

    try:
        msg = _client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=400,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}]
        )
        # Extract text from response
        text_parts = [block.text for block in msg.content if hasattr(block, "text")]
        return "\n".join(text_parts).strip()
    except Exception as e:
        # Fallback without web search
        if side == "BUY":
            return f"My decision: I bought {symbol} at ${price:.2f}\n\nMy reason: {rationale} I allocated ${notional:.0f} to this position based on current momentum signals.\n\nWhat I expect: If the momentum continues, this position should generate positive returns in the near term."
        else:
            pnl_str = f"+${realized_pnl:.2f}" if realized_pnl and realized_pnl > 0 else f"-${abs(realized_pnl):.2f}" if realized_pnl else "$0.00"
            return f"My decision: I sold {symbol} at ${price:.2f} — Realized {pnl_str}\n\nMy reason: {rationale}\n\nWhat I learned: This trade reflects my strategy's systematic approach to position management."
