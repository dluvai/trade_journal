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
"""
import concurrent.futures
import html
import json
import re
import time
import urllib.parse
import urllib.request

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
    return _cached("quotes", 8, _fetch_quotes)


def _fetch_news_for_queries(queries, limit=15):
    seen = {}
    for q in queries:
        try:
            data = _get_json(
                "https://query1.finance.yahoo.com/v1/finance/search",
                {"q": q, "newsCount": 8, "quotesCount": 0},
            )
        except Exception:
            continue
        for item in data.get("news", []):
            uid = item.get("uuid")
            if not uid or uid in seen or not item.get("title"):
                continue
            seen[uid] = {
                "title": item["title"],
                "publisher": item.get("publisher") or "",
                "link": item.get("link") or "",
                "time": item.get("providerPublishTime") or 0,
            }
    return sorted(seen.values(), key=lambda x: x["time"], reverse=True)[:limit]


# Per-currency queries for the Bias Check news panel -- verified by hand.
# USD-first pairs (USDJPY, USDCHF) and gold only ever return Yahoo's generic
# "trending" fallback regardless of phrasing (tried "USDJPY forex", "yen
# dollar", "Bank of Japan yen", all identical junk) -- so JPY and CHF fall
# back to the combined majors feed below rather than a currency-specific
# query that doesn't actually exist.
CURRENCY_NEWS_QUERIES = {
    "EUR": ["EURUSD=X"],
    "GBP": ["GBPUSD=X"],
    "AUD": ["AUDUSD=X"],
    "NZD": ["NZDUSD=X"],
}
_MAJORS_FEED = ["EURUSD=X", "GBPUSD=X", "AUDUSD=X", "NZDUSD=X"]


def get_currency_news(currency):
    queries = CURRENCY_NEWS_QUERIES.get(currency, _MAJORS_FEED)
    return _cached(f"news_{currency}", 300, lambda: _fetch_news_for_queries(queries))


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
