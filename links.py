"""Direct links to official sources.

Many of these sites block scraping or need a login, so the app cannot read them itself.
Instead it shows the right links next to each answer so users can open the source directly.
"""
import re
from urllib.parse import quote

# Shown in the "Direct links" panel (grouped for the UI).
DIRECTORY = {
    "📈 Stocks & exchanges": [
        ("NSE India", "https://www.nseindia.com"),
        ("BSE India", "https://www.bseindia.com"),
        ("Groww", "https://groww.in"),
        ("Zerodha", "https://zerodha.com"),
        ("Zerodha Varsity (learn)", "https://zerodha.com/varsity/"),
        ("Moneycontrol", "https://www.moneycontrol.com"),
        ("Screener.in", "https://www.screener.in"),
        ("TradingView", "https://www.tradingview.com"),
        ("Google Finance", "https://www.google.com/finance"),
        ("Yahoo Finance", "https://finance.yahoo.com"),
    ],
    "🧾 Company results": [
        ("BSE announcements", "https://www.bseindia.com/corporates/ann.html"),
        ("NSE financial results", "https://www.nseindia.com/companies-listing/corporate-filings-financial-results"),
        ("Moneycontrol earnings", "https://www.moneycontrol.com/markets/earnings/"),
        ("Screener.in", "https://www.screener.in"),
        ("SEC EDGAR (US filings)", "https://www.sec.gov/edgar"),
    ],
    "🚀 IPOs": [
        ("Chittorgarh", "https://www.chittorgarh.com"),
        ("NSE upcoming issues", "https://www.nseindia.com/market-data/all-upcoming-issues-ipo"),
        ("Groww IPO", "https://groww.in/ipo"),
        ("SEBI", "https://www.sebi.gov.in"),
    ],
    "🏦 Mutual funds": [
        ("AMFI (official NAV)", "https://www.amfiindia.com"),
        ("Value Research", "https://www.valueresearchonline.com"),
        ("Groww Mutual Funds", "https://groww.in/mutual-funds"),
    ],
    "🥇 Bullion": [
        ("IBJA (India benchmark)", "https://ibjarates.com"),
        ("MCX", "https://www.mcxindia.com"),
        ("LBMA", "https://www.lbma.org.uk"),
        ("Kitco", "https://www.kitco.com"),
        ("World Gold Council", "https://www.gold.org"),
    ],
    "🛢️ Crude oil": [
        ("MCX", "https://www.mcxindia.com"),
        ("EIA (US govt)", "https://www.eia.gov/petroleum/"),
        ("OPEC", "https://www.opec.org"),
        ("OilPrice.com", "https://oilprice.com"),
    ],
    "💱 Forex & money market": [
        ("RBI", "https://www.rbi.org.in"),
        ("FBIL (official INR rates)", "https://www.fbil.org.in"),
        ("CCIL", "https://www.ccilindia.com"),
        ("Investing.com currencies", "https://www.investing.com/currencies/"),
    ],
    "📰 News: India": [
        ("Economic Times", "https://economictimes.indiatimes.com/markets"),
        ("Financial Express", "https://www.financialexpress.com/market/"),
        ("Business Standard", "https://www.business-standard.com/markets"),
        ("Mint", "https://www.livemint.com/market"),
        ("Moneycontrol news", "https://www.moneycontrol.com/news/business/markets/"),
    ],
    "🌍 News: global": [
        ("Reuters", "https://www.reuters.com/markets/"),
        ("Bloomberg", "https://www.bloomberg.com/markets"),
        ("CNBC", "https://www.cnbc.com/markets/"),
    ],
}

_BULLION = [d for d in DIRECTORY["🥇 Bullion"] if d[0].startswith(("IBJA", "MCX", "Kitco"))]
_CRUDE = [d for d in DIRECTORY["🛢️ Crude oil"] if d[0].startswith(("MCX", "EIA", "OilPrice"))]
_FX = DIRECTORY["💱 Forex & money market"]
_MONEY = [d for d in _FX if d[0].startswith(("RBI", "CCIL", "FBIL"))]
_NEWS_IN = [d for d in DIRECTORY["📰 News: India"] if not d[0].startswith("Moneycontrol")] + [
    ("Moneycontrol", "https://www.moneycontrol.com")]
_NEWS_GLOBAL = DIRECTORY["🌍 News: global"][:2]

_KW = {
    "bullion": r"\b(gold|silver|bullion|platinum)\b",
    "crude": r"\b(crude|brent|wti|oil|natural gas|opec)\b",
    "fx": r"\b(usd|inr|eur|gbp|jpy|rupee|forex|dollar|exchange rate|currency)\b",
    "funds": r"\b(mutual funds?|nav|amc|elss|flexi cap|index funds?)\b",
    "ipo": r"\b(ipos?|gmp|listing|allotment|subscription|price band)\b",
    "results": r"\b(results?|earnings|quarter(ly)?|q[1-4]|profit|revenue|balance sheet|financials?)\b",
    "money": r"\b(rbi|repo|mibor|yield|g-?sec|t-?bills?|money market|liquidity|call rate|crr|slr|bonds?)\b",
    "global": r"\b(us|fed|wall street|global|nasdaq|s&p|dow|europe|china)\b",
}


def links_for(plan, question: str, limit: int = 10) -> list:
    """Context-aware links (label, url) for the answer, built from the plan and the question."""
    q = (question or "").lower()
    hit = {k: bool(re.search(p, q)) for k, p in _KW.items()}
    out = []

    def add(*items):
        for it in items:
            if it not in out:
                out.append(it)

    for sym in plan.stocks[:3]:
        add((f"Yahoo Finance · {sym}", f"https://finance.yahoo.com/quote/{quote(sym, safe='')}/"))
        if sym.endswith(".NS"):
            base = sym[:-3]
            add((f"NSE · {base}", f"https://www.nseindia.com/get-quotes/equity?symbol={quote(base)}"),
                (f"Screener · {base}", f"https://www.screener.in/company/{quote(base)}/"))
        elif sym.endswith(".BO") or sym == "^BSESN":
            add(("BSE India", "https://www.bseindia.com"))
        elif sym in ("^NSEI", "^NSEBANK"):
            add(("NSE live indices", "https://www.nseindia.com/market-data/live-market-indices"))

    cs = " ".join(plan.commodities).lower()
    if hit["bullion"] or re.search(r"gold|silver|platinum", cs):
        add(*_BULLION)
    if hit["crude"] or re.search(r"crude|brent|wti|natural gas", cs):
        add(*_CRUDE)
    if plan.fx or hit["fx"]:
        add(*_FX[:2], _FX[3])
    if plan.funds or hit["funds"]:
        add(*DIRECTORY["🏦 Mutual funds"])
    if hit["ipo"]:
        add(*DIRECTORY["🚀 IPOs"][:3])
    if hit["results"]:
        add(*DIRECTORY["🧾 Company results"][:3])
    if hit["money"]:
        add(*_MONEY)
    if plan.news_queries or plan.deep_news:
        add(*_NEWS_IN[:4])
        if hit["global"]:
            add(*_NEWS_GLOBAL)
    return out[:limit]
