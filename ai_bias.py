"""
AI bias check: combines your written strategy, the macro snapshot comparison
(see fundamentals.py), and an optional chart screenshot into one prompt for
Claude, and returns a written technical + fundamental read.

Requires an Anthropic API key on this machine -- set it as an environment
variable before starting server.py:

    Windows (PowerShell), current session only:
        $env:ANTHROPIC_API_KEY = "sk-ant-..."

    To persist it across restarts, set it as a normal Windows environment
    variable (System Properties -> Environment Variables) instead of typing
    it anywhere in chat or committing it to a file.

Nothing here stores or logs your key -- it's read fresh from the environment
on every call.
"""
import base64
import os
import urllib.request

MODEL = "claude-sonnet-5"


def _fetch_image_b64(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = resp.read()
        content_type = resp.headers.get_content_type() or "image/png"
    if content_type not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        content_type = "image/png"
    return base64.b64encode(data).decode("ascii"), content_type


def build_prompt(pair, strategy_text, comparison, chart_present):
    lines = [
        f"You are helping a discretionary trader think through a potential trade on {pair}.",
        "", "## Their stated strategy",
    ]
    if strategy_text and strategy_text.strip():
        lines.append(strategy_text.strip())
    else:
        lines.append(
            "(No strategy text has been written yet -- note this explicitly and give a "
            "generic technical/fundamental read instead of a strategy-fit judgement.)"
        )

    lines.append("")
    if comparison and comparison.get("supported"):
        base, quote = comparison["base"], comparison["quote"]
        lines.append(f"## Macro snapshot: {base} vs {quote}")
        for row in comparison["scored_rows"] + [comparison["cpi_row"]]:
            lines.append(f"- {row['label']}: {base} = {row['base_value']}, {quote} = {row['quote_value']}")
        lines.append(
            f"On the four directly-comparable metrics (rate, GDP, unemployment, PMI), "
            f"{base} leads on {comparison['score']['base']} and {quote} leads on {comparison['score']['quote']}."
        )
        if comparison.get("base_bias") or comparison.get("quote_bias"):
            lines.append(
                f"Manually-set fundamental bias: {base} = {comparison.get('base_bias') or 'not set'}, "
                f"{quote} = {comparison.get('quote_bias') or 'not set'}."
            )
    else:
        lines.append("## Macro snapshot")
        lines.append("No macro data has been entered yet for one or both currencies in this pair.")

    lines.append("")
    if chart_present:
        lines.append("A chart screenshot is attached -- use it for the technical read.")
    else:
        lines.append("No chart was provided -- skip the technical section rather than guessing.")

    lines += [
        "",
        "Write a concise bias assessment with three short sections:",
        "1. **Fundamental read** -- which currency looks stronger and why, referencing the "
        "specific metrics above. Explain in plain terms how each metric feeds into that "
        "economy's strength and why that matters for this exchange rate -- this trader wants "
        "to understand the mechanism, not just the conclusion.",
        "2. **Technical read** -- only if a chart was provided.",
        "3. **Fit with their strategy** -- does the current picture line up with the rules they "
        "wrote above? Be honest if it's unclear or mixed; don't force a fit.",
        "",
        "Keep it tight -- a trader reading this between charts, not an essay. This is analysis "
        "to help their own decision, not financial advice -- do not instruct them to place a "
        "specific trade or size a position.",
    ]
    return "\n".join(lines)


def run_bias_check(pair, strategy_text, comparison, chart_link):
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
    content = [{"type": "text", "text": build_prompt(pair, strategy_text, comparison, bool(chart_link))}]

    if chart_link:
        try:
            b64, content_type = _fetch_image_b64(chart_link)
            content.insert(0, {
                "type": "image",
                "source": {"type": "base64", "media_type": content_type, "data": b64},
            })
        except Exception as e:
            content.append({"type": "text", "text": f"(Note: the chart image could not be loaded -- {e})"})

    response = client.messages.create(
        model=MODEL,
        max_tokens=900,
        messages=[{"role": "user", "content": content}],
    )
    return "".join(block.text for block in response.content if block.type == "text")
