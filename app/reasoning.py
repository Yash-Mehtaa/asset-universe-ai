"""Trade reasoning module. Generates plain-English explanations
using the strategy's built-in rationale. No API calls."""
from __future__ import annotations


def generate_trade_reasoning(trade) -> str:
    """Generate plain-English reasoning for a trade based on strategy rationale."""
    side = trade.side.upper()
    symbol = trade.symbol
    price = trade.price
    notional = trade.notional
    rationale = trade.rationale or ""
    realized_pnl = trade.realized_pnl

    if side == "BUY":
        return (
            f"My decision: I bought {symbol} at ${price:.2f}\n\n"
            f"My reason: {rationale} Allocated ${notional:,.0f} to this position.\n\n"
            f"What I expect: If the signal holds, this position should generate positive returns in the near term."
        )
    else:
        if realized_pnl is not None and realized_pnl > 0:
            pnl_str = f"+${realized_pnl:,.2f}"
            outcome = "Profitable exit — strategy worked as designed."
        elif realized_pnl is not None and realized_pnl < 0:
            pnl_str = f"-${abs(realized_pnl):,.2f}"
            outcome = "Loss taken — strategy cut the position to protect capital."
        else:
            pnl_str = "$0.00"
            outcome = "Position closed at breakeven."

        return (
            f"My decision: I sold {symbol} at ${price:.2f} — Realized {pnl_str}\n\n"
            f"My reason: {rationale}\n\n"
            f"What I learned: {outcome}"
        )