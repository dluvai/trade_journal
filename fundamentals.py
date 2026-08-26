"""
Deterministic currency-strength comparison from the manual macro snapshot.
No AI call involved -- this is pure arithmetic over whatever you've filled
in on the Strategy tab, so it's free and instant. The AI bias check (see
ai_bias.py) uses this as its factual basis rather than guessing numbers.
"""

METRIC_LABELS = {
    "interest_rate": "Interest rate",
    "gdp_yoy": "GDP growth (YoY)",
    "unemployment": "Unemployment rate",
    "pmi": "PMI (manufacturing)",
    "cpi_yoy": "CPI (YoY inflation)",
}

METRIC_EXPLAINERS = {
    "interest_rate": (
        "Higher rates attract yield-seeking capital, which tends to support a currency; "
        "central banks raise rates to fight inflation or cool an overheating economy."
    ),
    "gdp_yoy": (
        "Faster GDP growth signals a healthier economy and more attractive returns for "
        "foreign investors -- generally currency-positive."
    ),
    "unemployment": (
        "A lower unemployment rate signals a tighter labor market and a stronger economy; "
        "rising unemployment is a classic recession warning and tends to weigh on the currency."
    ),
    "pmi": (
        "A PMI above 50 signals expansion in manufacturing activity, below 50 signals "
        "contraction. Rising PMI usually supports the currency as it points to accelerating "
        "economic activity."
    ),
    "cpi_yoy": (
        "Rising inflation above target often pushes the central bank toward tightening "
        "(rate hikes), which can be currency-supportive in the near term -- but persistently "
        "high inflation erodes purchasing power and can weaken the currency over the long run. "
        "Shown for context only; not scored, because direction depends on where inflation sits "
        "relative to target."
    ),
}

# (metric key, higher value = stronger currency)
SCORED_METRICS = [
    ("interest_rate", True),
    ("gdp_yoy", True),
    ("unemployment", False),
    ("pmi", True),
]


def parse_pair(pair):
    pair = (pair or "").upper().strip()
    if len(pair) == 6 and pair.isalpha():
        return pair[:3], pair[3:]
    return None, None


def compare_pair(pair, macro_rows):
    base, quote = parse_pair(pair)
    if not base:
        return None

    by_ccy = {r["currency"]: r for r in macro_rows}
    base_row = by_ccy.get(base)
    quote_row = by_ccy.get(quote)
    if not base_row or not quote_row:
        return {"pair": pair, "base": base, "quote": quote, "supported": False}

    scored_rows = []
    score = {"base": 0, "quote": 0}
    for key, higher_is_stronger in SCORED_METRICS:
        bv, qv = base_row.get(key), quote_row.get(key)
        edge = None
        if bv is not None and qv is not None and bv != qv:
            edge = "base" if (bv > qv) == higher_is_stronger else "quote"
            score[edge] += 1
        scored_rows.append({
            "metric": key, "label": METRIC_LABELS[key],
            "base_value": bv, "quote_value": qv, "edge": edge,
            "note": METRIC_EXPLAINERS[key],
        })

    cpi_row = {
        "metric": "cpi_yoy", "label": METRIC_LABELS["cpi_yoy"],
        "base_value": base_row.get("cpi_yoy"), "quote_value": quote_row.get("cpi_yoy"),
        "edge": None, "note": METRIC_EXPLAINERS["cpi_yoy"],
    }

    result = {
        "pair": pair, "base": base, "quote": quote, "supported": True,
        "score": score, "scored_rows": scored_rows, "cpi_row": cpi_row,
        "base_bias": base_row.get("bias"), "quote_bias": quote_row.get("bias"),
    }
    result["verdict"] = generate_verdict(result)
    return result


def generate_verdict(comparison):
    """A written conclusion generated purely from the scored metrics -- no AI call.
    This is the standard, textbook direction each metric pushes a currency
    (see METRIC_EXPLAINERS); it's a starting read, not a guarantee."""
    base, quote = comparison["base"], comparison["quote"]
    rows = comparison["scored_rows"]
    score = comparison["score"]
    scored_count = sum(1 for r in rows if r["edge"])

    if scored_count == 0:
        return (
            f"{base} and {quote} are tied on every metric entered so far, or there isn't "
            f"enough data yet -- fill in interest rate, GDP, unemployment, or PMI for both "
            f"in the macro snapshot below."
        )

    base_reasons = [r["label"].lower() for r in rows if r["edge"] == "base"]
    quote_reasons = [r["label"].lower() for r in rows if r["edge"] == "quote"]

    def join(items):
        return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"

    if score["base"] == score["quote"]:
        sentence = (
            f"{base} and {quote} are evenly matched on the metrics entered so far "
            f"({score['base']}-{score['quote']} on {scored_count} scored metric{'s' if scored_count != 1 else ''})."
        )
    else:
        if score["base"] > score["quote"]:
            leader, trailer, lead_reasons, trail_reasons, lead_n, trail_n = (
                base, quote, base_reasons, quote_reasons, score["base"], score["quote"],
            )
        else:
            leader, trailer, lead_reasons, trail_reasons, lead_n, trail_n = (
                quote, base, quote_reasons, base_reasons, score["quote"], score["base"],
            )
        sentence = f"{leader} looks fundamentally stronger than {trailer} right now ({lead_n}-{trail_n} on {scored_count} scored metrics)."
        if lead_reasons:
            sentence += f" {leader} has the edge on {join(lead_reasons)}."
        if trail_reasons:
            sentence += f" {trailer}'s only edge is {join(trail_reasons)}."

    cpi = comparison["cpi_row"]
    if cpi["base_value"] is not None and cpi["quote_value"] is not None and cpi["base_value"] != cpi["quote_value"]:
        hotter = base if cpi["base_value"] > cpi["quote_value"] else quote
        hv = cpi["base_value"] if hotter == base else cpi["quote_value"]
        cv = cpi["quote_value"] if hotter == base else cpi["base_value"]
        sentence += (
            f" Inflation is running hotter in {hotter} ({hv}% vs {cv}%) -- not scored directly since "
            f"it cuts both ways, but worth watching for how it shapes the next rate decision."
        )

    return sentence
