"""
Deterministic currency-strength comparison from the manual macro snapshot.
No AI call involved -- this is pure arithmetic over whatever you've filled
in on the Strategy tab, so it's free and instant. The AI bias check (see
ai_bias.py) uses this as its factual basis rather than guessing numbers.
"""

METRIC_LABELS = {
    "interest_rate": "Interest rate",
    "gdp_yoy": "GDP growth (YoY)",
    "gdp_mom": "GDP growth (MoM)",
    "unemployment": "Unemployment rate",
    "pmi": "PMI (manufacturing)",
    "cpi_yoy": "CPI (YoY inflation)",
    "cpi_mom": "CPI (MoM inflation)",
    "core_cpi_yoy": "Core CPI (YoY)",
    "core_ppi_yoy": "Core PPI (YoY)",
    "ppi_yoy": "PPI (YoY)",
    "core_pce_yoy": "Core PCE (YoY)",
    "core_pce_mom": "Core PCE (MoM)",
    "employment_change": "Employment change",
    "retail_sales_yoy": "Retail sales (YoY)",
    "trade_balance": "Trade balance",
    "current_account": "Current account",
    "current_account_pct_gdp": "Current account (% of GDP)",
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
    "gdp_mom": (
        "A more immediate, noisier read on growth than the annual figure -- the UK is the only "
        "major economy that publishes GDP monthly rather than just quarterly."
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
        "Hotter inflation raises the odds of the central bank tightening (rate hikes) to cool "
        "it, which is typically currency-supportive in the near term -- the same logic as the "
        "policy rate itself, just one step upstream of it."
    ),
    "cpi_mom": (
        "Month-over-month inflation -- a noisier, more immediate read than the annual figure; "
        "a hot print raises the odds of near-term rate-hike pressure."
    ),
    "core_cpi_yoy": (
        "Strips out volatile food and energy prices -- the central bank's preferred inflation "
        "gauge for policy decisions, since it reflects underlying price pressure rather than "
        "one-off swings."
    ),
    "core_ppi_yoy": (
        "Pipeline inflation at the producer level -- rising producer costs often get passed "
        "through to consumer prices later, making this a leading indicator for future CPI."
    ),
    "ppi_yoy": (
        "The headline producer price index -- unlike core_ppi_yoy (a US-specific ex-food-and-"
        "energy construct), this is the all-items producer price gauge most other countries "
        "actually publish, and is scored the same way: a leading indicator for future CPI."
    ),
    "core_pce_yoy": (
        "The Fed's own preferred inflation gauge, distinct from CPI -- weighted differently and "
        "adjusted for consumers substituting cheaper goods, which the Fed considers a more "
        "accurate read on underlying inflation."
    ),
    "core_pce_mom": (
        "Month-over-month core PCE -- the same Fed-preferred gauge as Core PCE YoY, just the "
        "more immediate monthly read."
    ),
    "employment_change": (
        "The net change in jobs for the month -- a strong print signals a resilient labor "
        "market and economic momentum (currency-positive); a weak or negative print raises "
        "recession concern."
    ),
    "retail_sales_yoy": (
        "Consumer spending makes up the bulk of most economies' GDP -- rising retail sales "
        "signal healthy demand and economic momentum."
    ),
    "trade_balance": (
        "A surplus (more exports than imports) means net foreign demand for the currency to "
        "pay for those exports -- currency-supportive; a persistent deficit works the other way."
    ),
    "current_account": (
        "The broadest measure of transactions with the rest of the world (trade plus investment "
        "income) -- a surplus reflects net foreign demand for the currency; a persistent deficit "
        "typically needs financing by capital inflows, which can pressure it lower over time."
    ),
    "current_account_pct_gdp": (
        "Same concept as current account, scaled to the size of the economy -- deliberately a "
        "separate field from current_account (which is USD-only, in raw dollars) rather than "
        "forcing a live FX conversion into a shared field; comparing as a share of GDP is also "
        "the standard way economists compare external balances across differently-sized economies."
    ),
}

# (metric key, higher value = stronger currency). Every macro metric the app
# tracks gets scored the same way -- included in a given pair's comparison
# only when both currencies actually have a value for it (see compare_pair),
# so nothing is ever shown half-resolved. Unemployment is the only metric
# where a lower reading is the stronger one; every other tracked metric
# follows "higher is currency-positive."
SCORED_METRICS = [
    ("interest_rate", True),
    ("gdp_yoy", True),
    ("gdp_mom", True),
    ("unemployment", False),
    ("pmi", True),
    ("cpi_yoy", True),
    ("cpi_mom", True),
    ("core_cpi_yoy", True),
    ("core_ppi_yoy", True),
    ("ppi_yoy", True),
    ("core_pce_yoy", True),
    ("core_pce_mom", True),
    ("employment_change", True),
    ("retail_sales_yoy", True),
    ("trade_balance", True),
    ("current_account", True),
    ("current_account_pct_gdp", True),
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

    # Only metrics both currencies actually have a real, distinct value for
    # make the cut -- a metric missing on either side, or tied, has nothing
    # meaningful to compare, so it's dropped entirely rather than shown as
    # an unresolved "context only" row.
    scored_rows = []
    score = {"base": 0, "quote": 0}
    for key, higher_is_stronger in SCORED_METRICS:
        bv, qv = base_row.get(key), quote_row.get(key)
        if bv is None or qv is None or bv == qv:
            continue
        edge = "base" if (bv > qv) == higher_is_stronger else "quote"
        score[edge] += 1
        scored_rows.append({
            "metric": key, "label": METRIC_LABELS[key],
            "base_value": bv, "quote_value": qv, "edge": edge,
            "note": METRIC_EXPLAINERS[key],
        })

    result = {
        "pair": pair, "base": base, "quote": quote, "supported": True,
        "score": score, "scored_rows": scored_rows,
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
    scored_count = len(rows)

    if scored_count == 0:
        return (
            f"{base} and {quote} are tied on every metric entered so far, or there isn't enough "
            f"data yet -- fill in at least one shared macro metric for both in the workspace below."
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

    return sentence
