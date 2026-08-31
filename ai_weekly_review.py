"""
AI weekly review: summarizes patterns in one week's trades and reflection
notes using Claude -- what happened, what's recurring in the notes, and one
concrete thing to focus on next week.

Requires an Anthropic API key on this machine -- see ai_bias.py for how to
set it. Nothing here stores or logs your key -- it's read fresh from the
environment on every call.
"""
import os

MODEL = "claude-sonnet-5"


def build_review_prompt(trades, week_start, week_end):
    lines = [
        f"You are reviewing one trader's week of trading, {week_start} to {week_end}.",
        "",
        f"## This week's trades ({len(trades)} total)",
    ]
    if not trades:
        lines.append("No trades were logged this week.")
    else:
        wins = sum(1 for t in trades if t["result"] == "WIN")
        losses = sum(1 for t in trades if t["result"] == "LOSS")
        be = sum(1 for t in trades if t["result"] == "BE")
        total_pnl = sum(t["pnl"] for t in trades)
        lines.append(f"{wins} wins, {losses} losses, {be} breakeven -- net return {total_pnl * 100:.2f}%.")
        lines.append("")
        lines.append("Trade-by-trade:")
        for t in trades:
            note = f" -- notes: {t['notes'].strip()}" if t.get("notes") and t["notes"].strip() else ""
            lines.append(f"- {t['date']} {t['pair']} {t['direction']} {t['result']} ({t['pnl'] * 100:+.2f}%){note}")

    lines += [
        "",
        "Write a concise weekly review with three short sections. Do not include a title or "
        "heading line (the date range is already shown separately) -- start directly with "
        "section 1.",
        "1. **What happened** -- the headline numbers, in plain terms.",
        "2. **Patterns worth noticing** -- anything recurring in the notes (emotional language, "
        "repeated mistakes, a setup that's working or not), referencing specific trades above. "
        "If there isn't a real pattern, say so rather than inventing one.",
        "3. **One thing to focus on next week** -- a single, concrete, actionable suggestion, not "
        "a generic pep talk.",
        "",
        "Keep it tight -- a trader reading this on a Sunday night, not an essay. This is "
        "reflection support, not financial advice -- do not suggest specific future trades.",
    ]
    return "\n".join(lines)


def generate_weekly_review(trades, week_start, week_end):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set for this server process. "
            "Set it as an environment variable and restart server.py -- see ai_bias.py for how."
        )
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("The 'anthropic' package isn't installed. Run: pip install anthropic")

    client = anthropic.Anthropic(api_key=api_key)
    prompt = build_review_prompt(trades, week_start, week_end)
    response = client.messages.create(
        model=MODEL,
        max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in response.content if block.type == "text")
