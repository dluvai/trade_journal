"""
Live price ticker + market news, sourced from Yahoo Finance's public
chart/search endpoints -- no API key needed, same no-signup approach as
fred_sync.py. These are undocumented-but-stable endpoints that yfinance and
plenty of other free tools rely on; a browser-like User-Agent is required or
Yahoo answers with 401/429.

XAUUSD has no free chart symbol on Yahoo, so gold uses its COMEX future
(GC=F) instead -- trades a few dollars off spot but tracks it closely. The
two indices use the cash index (^NDX, ^GSPC) as the free proxy for a
broker's NAS100/US500 CFD quote -- CFDs track the cash index plus a small
funding basis, not the futures curve, so this is the closer of the two.

Both fetchers cache their result in memory for a short window so a
dashboard left open polling every 20-30s doesn't hammer Yahoo on every tab.

Currency news is filtered twice before it reaches the dashboard: anything
older than 2 days is dropped outright, then a Claude call judges which of
what's left is actually likely to move a currency (see
_filter_market_moving) -- routine single-company earnings and generic
market wrap-ups get filtered out rather than cluttering the panel.
"""
import concurrent.futures
import html
import json
import os
import re
import time
import urllib.parse
import urllib.request

import ai_bias

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

SYMBOLS = [
    ("EURUSD=X", "EUR/USD"),
    ("GBPUSD=X", "GBP/USD"),
    ("USDJPY=X", "USD/JPY"),
    ("USDCHF=X", "USD/CHF"),
    ("AUDUSD=X", "AUD/USD"),
    ("USDCAD=X", "USD/CAD"),
    ("NZDUSD=X", "NZD/USD"),
    ("GC=F", "Gold"),
    ("^NDX", "NAS100"),
    ("^GSPC", "US500"),
]

_cache = {}


def _cached(key, ttl_seconds, fn):
    hit = _cache.get(key)
    now = time.time()
    if hit and now - hit[0] < ttl_seconds:
        return hit[1]
    value = fn()
    _cache[key] = (now, value)
    return value


def _get_json(url, params=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_one_quote(job):
    symbol, label = job
    entry = {"symbol": symbol, "label": label, "price": None, "change": None, "changePct": None}
    try:
        meta = _get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}")["chart"]["result"][0]["meta"]
        price = meta.get("regularMarketPrice")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is not None and prev:
            entry["price"] = price
            entry["change"] = price - prev
            entry["changePct"] = (price - prev) / prev * 100
    except Exception:
        pass  # leave this one symbol blank rather than fail the whole ticker
    return entry


def _fetch_quotes():
    # 10 independent requests -- same reasoning as fred_sync.sync(): these
    # are network waits, so a small thread pool gets all 10 back in roughly
    # the time of the single slowest one instead of the sum of all ten.
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_fetch_one_quote, SYMBOLS))
    by_symbol = {r["symbol"]: r for r in results}
    return [by_symbol[symbol] for symbol, _ in SYMBOLS]


def get_quotes():
    return _cached("quotes", 3, _fetch_quotes)


NEWS_MAX_AGE_SECONDS = 2 * 24 * 3600  # 2 days -- older than that isn't "latest news" anymore


def _fetch_news_for_queries(queries, limit=20):
    seen = {}
    cutoff = time.time() - NEWS_MAX_AGE_SECONDS
    for q in queries:
        try:
            data = _get_json(
                "https://query1.finance.yahoo.com/v1/finance/search",
                # Fetch more than we'll keep -- the 2-day age filter and the
                # market-moving filter below both throw articles away, so a
                # tight per-query count would leave a currency with hardly
                # anything left on a quiet news day.
                {"q": q, "newsCount": 20, "quotesCount": 0},
            )
        except Exception:
            continue
        for item in data.get("news", []):
            uid = item.get("uuid")
            pub_time = item.get("providerPublishTime") or 0
            if not uid or uid in seen or not item.get("title") or pub_time < cutoff:
                continue
            seen[uid] = {
                "uuid": uid,
                "title": item["title"],
                "publisher": item.get("publisher") or "",
                "link": item.get("link") or "",
                "time": pub_time,
            }
    return sorted(seen.values(), key=lambda x: x["time"], reverse=True)[:limit]


# Cached per article uuid (Yahoo's own id, always unique -- unlike link,
# which is occasionally blank and would otherwise collide multiple unrelated
# articles onto the same cache entry). An article's importance doesn't
# change once classified, so this never needs to expire, only grow
# (negligible for a personal dashboard's news volume).
_importance_cache = {}


def _filter_market_moving(items):
    """Keep only headlines a Claude call judges likely to actually move a
    major currency -- filters out routine single-company earnings, opinion
    columns, and generic "markets today" wraps that would otherwise clutter
    the panel. Fails open (keeps everything) if no ANTHROPIC_API_KEY is
    configured or the call errors or times out, rather than silently
    showing an empty panel because of a missing key or a transient API
    hiccup."""
    if not items:
        return items
    uncached = [it for it in items if it["uuid"] not in _importance_cache]
    if uncached:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            for it in uncached:
                _importance_cache[it["uuid"]] = True
        else:
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=api_key)
                numbered = "\n".join(f"{i + 1}. {it['title']} ({it['publisher']})" for i, it in enumerate(uncached))
                prompt = (
                    "You're filtering a forex trader's news feed. For each numbered headline "
                    "below, decide whether it's genuinely likely to move a major currency -- "
                    "things like interest rate/inflation/employment/GDP data and commentary, "
                    "central bank action, elections, or geopolitical shocks involving major "
                    "economies (war, sanctions, major conflict escalation/de-escalation, oil "
                    "supply shocks). Routine single-company earnings, generic market wrap-ups, "
                    "opinion columns, and minor data revisions are NOT market-moving.\n\n"
                    "Reply with ONLY a JSON array of true/false, same order and length as the "
                    f"list, nothing else.\n\n{numbered}"
                )
                response = client.messages.create(
                    model=ai_bias.MODEL, max_tokens=500, timeout=15,
                    messages=[{"role": "user", "content": prompt}],
                )
                text = "".join(b.text for b in response.content if b.type == "text")
                flags = json.loads(re.search(r"\[.*\]", text, re.DOTALL).group(0))
                for it, flag in zip(uncached, flags):
                    _importance_cache[it["uuid"]] = bool(flag)
            except Exception:
                for it in uncached:
                    _importance_cache.setdefault(it["uuid"], True)
    return [it for it in items if _importance_cache.get(it["uuid"], True)]


# Per-currency queries for the Bias Check news panel -- verified by hand.
# USD-first pairs (USDJPY, USDCHF, USDCAD) and USD's own dollar-index symbol
# only ever return Yahoo's generic "trending" fallback regardless of
# phrasing (tried "USDJPY forex", "yen dollar", "Bank of Japan yen",
# "DX-Y.NYB", "dollar index", "loonie" -- all either identical junk or
# nothing) -- so USD, JPY, CHF, and CAD all share the combined majors feed
# below rather than a currency-specific query that doesn't actually exist.
CURRENCY_NEWS_QUERIES = {
    "EUR": ["EURUSD=X"],
    "GBP": ["GBPUSD=X"],
    "AUD": ["AUDUSD=X"],
    "NZD": ["NZDUSD=X"],
}
_MAJORS_FEED = ["EURUSD=X", "GBPUSD=X", "AUDUSD=X", "NZDUSD=X"]

# For the four currencies sharing _MAJORS_FEED, the pool of articles is
# identical no matter which one you're viewing -- there's no way around
# that with this data source, but the ORDER doesn't have to be. Re-ranking
# toward each currency's own keywords means JPY's panel actually surfaces
# whatever yen/BOJ-relevant items exist in that shared pool first, instead
# of every one of the four showing the exact same top headlines.
_RELEVANCE_KEYWORDS = {
    "USD": ["dollar", "fed ", "federal reserve", "fomc", "u.s.", "treasury", "powell"],
    "JPY": ["yen", "japan", "boj", "bank of japan"],
    "CHF": ["franc", "swiss", "snb", "switzerland"],
    "CAD": ["loonie", "canada", "canadian", "boc", "bank of canada"],
}


def _rerank_by_relevance(items, currency):
    keywords = _RELEVANCE_KEYWORDS.get(currency)
    if not keywords:
        return items
    def score(it):
        title = it["title"].lower()
        return sum(1 for k in keywords if k in title)
    return sorted(items, key=score, reverse=True)


def get_currency_news(currency):
    queries = CURRENCY_NEWS_QUERIES.get(currency, _MAJORS_FEED)
    items = _cached(f"news_{currency}", 60, lambda: _filter_market_moving(_fetch_news_for_queries(queries)))
    return _rerank_by_relevance(items, currency) if currency in _RELEVANCE_KEYWORDS else items


_DESCRIPTION_PATTERNS = [
    re.compile(r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']*)["\']', re.IGNORECASE),
    re.compile(r'<meta\s+content=["\']([^"\']*)["\']\s+property=["\']og:description["\']', re.IGNORECASE),
    re.compile(r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']*)["\']', re.IGNORECASE),
    re.compile(r'<meta\s+content=["\']([^"\']*)["\']\s+name=["\']description["\']', re.IGNORECASE),
]


def _fetch_article_summary(url):
    # The og:description / meta-description tag is a short blurb the
    # publisher writes specifically to be shown when the page is linked
    # elsewhere (link previews on iMessage, Slack, Twitter, etc. all read
    # the same tag) -- a legitimate, publisher-provided summary, not us
    # scraping the article body/paragraphs, which would raise real
    # copyright and ToS concerns across dozens of different publishers.
    if not url.startswith("https://"):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read(200_000)  # the <head> is always well within this; no need for the full page
        text = raw.decode("utf-8", errors="ignore")
        for pattern in _DESCRIPTION_PATTERNS:
            m = pattern.search(text)
            if m and m.group(1).strip():
                return html.unescape(m.group(1).strip())
        return None
    except Exception:
        return None


def get_article_summary(url):
    return _cached(f"summary::{url}", 3600, lambda: _fetch_article_summary(url))


if __name__ == "__main__":
    print("Quotes:")
    for q in get_quotes():
        print(" ", q)
    print("\nUSD news:")
    for n in get_currency_news("USD"):
        print(" ", n["publisher"], "-", n["title"])
