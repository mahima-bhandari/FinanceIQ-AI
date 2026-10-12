import ast
import logging
import math
import operator
import re
from urllib.parse import quote_plus

import feedparser
import requests
import yfinance as yf
from langchain_core.tools import tool

logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # silence 404 spam


def clip(s: str, n: int = 1200) -> str:
    return s if len(s) <= n else s[:n] + "…[truncated]"


# ---------------------------------------------------------------- stocks & indices
ALIASES = {
    "nifty": "^NSEI", "nifty 50": "^NSEI", "nifty50": "^NSEI", "sensex": "^BSESN",
    "bank nifty": "^NSEBANK", "nifty bank": "^NSEBANK", "s&p 500": "^GSPC", "sp500": "^GSPC",
    "nasdaq": "^IXIC", "dow jones": "^DJI", "dow": "^DJI",
}


def _resolve(sym: str) -> str:
    """Turn a company name into a Yahoo ticker if the planner sent a name."""
    s = sym.strip()
    if s.lower() in ALIASES:
        return ALIASES[s.lower()]
    if s.startswith("^") or "." in s or "=" in s or (s.isupper() and " " not in s):
        return s
    try:
        for q in yf.Search(s, max_results=5).quotes:
            if q.get("quoteType") in ("EQUITY", "INDEX", "ETF") and q.get("symbol"):
                return q["symbol"]
    except Exception:
        pass
    return s


@tool
def stock_quote(symbol: str) -> str:
    """Latest quote for a stock or index. NSE: RELIANCE.NS, BSE: RELIANCE.BO.
    Indices: ^NSEI (Nifty50), ^BSESN (Sensex), ^NSEBANK, ^GSPC (S&P500), ^IXIC, ^DJI, ^FTSE, ^N225. US: AAPL."""
    sym = _resolve(symbol)
    try:
        t = yf.Ticker(sym)
        fi = t.fast_info
        last, prev = fi["last_price"], fi["previous_close"]
        out = (f"{sym}: {last:.2f} {fi['currency']} ({(last - prev) / prev * 100:+.2f}% vs prev close {prev:.2f}); "
               f"day {fi['day_low']:.2f}-{fi['day_high']:.2f}; 52w {fi['year_low']:.2f}-{fi['year_high']:.2f};")
    except Exception:
        return f"No quote available for '{symbol}' (tried {sym})."
    try:  # date lookup must never break the quote
        as_of = t.history(period="5d").index[-1].strftime("%d %b %Y")
    except Exception:
        as_of = "not available"
    return out + f" last trade date {as_of}."


# ---------------------------------------------------------------- commodities
COMMODITIES = {
    "gold": "GC=F", "silver": "SI=F", "platinum": "PL=F", "crude": "CL=F",
    "wti": "CL=F", "brent": "BZ=F", "natural gas": "NG=F", "copper": "HG=F",
}


@tool
def commodity_price(name: str) -> str:
    """Global futures price in USD for: gold, silver, platinum, crude/wti, brent, natural gas, copper.
    For gold/silver also returns an INR parity price (excludes import duty/GST)."""
    sym = COMMODITIES.get(name.lower().strip())
    if not sym:
        return f"Unknown commodity. Options: {', '.join(COMMODITIES)}"
    try:
        usd = yf.Ticker(sym).fast_info["last_price"]
        out = f"{name}: {usd:.2f} USD (futures {sym})."
        if name.lower().strip() in ("gold", "silver"):
            inr = yf.Ticker("USDINR=X").fast_info["last_price"]
            if name.lower().strip() == "gold":
                out += f" INR parity ≈ ₹{usd * inr / 31.1035 * 10:,.0f}/10g at USDINR {inr:.2f}."
            else:
                out += f" INR parity ≈ ₹{usd * inr / 31.1035 * 1000:,.0f}/kg at USDINR {inr:.2f}."
            out += " Actual MCX/IBJA/retail rates differ (duty, GST, premium)."
        return out
    except Exception as e:
        return f"Could not fetch {name}: {e}"


# ---------------------------------------------------------------- forex
@tool
def forex_rate(base: str, quote: str, amount: float = 1.0) -> str:
    """Daily ECB reference FX rate, e.g. base='USD', quote='INR'. Converts 'amount' of base."""
    try:
        r = requests.get(
            "https://api.frankfurter.dev/v1/latest",
            params={"base": base.upper(), "symbols": quote.upper(), "amount": amount},
            timeout=10,
        ).json()
        return f"{amount} {base.upper()} = {r['rates'][quote.upper()]} {quote.upper()} (ECB reference, {r['date']})"
    except Exception as e:
        return f"FX lookup failed: {e}"


# ---------------------------------------------------------------- mutual funds
@tool
def mutual_fund_nav(name: str) -> str:
    """Latest NAV of an Indian mutual fund (AMFI data via mfapi.in). Use full name, e.g. 'parag parikh flexi cap direct growth'."""
    try:
        hits = requests.get("https://api.mfapi.in/mf/search", params={"q": name}, timeout=10).json()
        if not hits:
            return "No scheme found."
        out = []
        for s in hits[:2]:
            d = requests.get(f"https://api.mfapi.in/mf/{s['schemeCode']}/latest", timeout=10).json()["data"][0]
            out.append(f"{s['schemeName']} | NAV {d['nav']} on {d['date']}")
        return clip("\n".join(out))
    except Exception as e:
        return f"NAV lookup failed: {e}"


# ---------------------------------------------------------------- news (Google News RSS)
SOURCES = {
    "economictimes": "economictimes.indiatimes.com", "financialexpress": "financialexpress.com",
    "businessstandard": "business-standard.com", "mint": "livemint.com", "moneycontrol": "moneycontrol.com",
    "groww": "groww.in", "reuters": "reuters.com", "bloomberg": "bloomberg.com",
    "nse": "nseindia.com", "bse": "bseindia.com",
}


def news_items(query: str, source: str = "any", n: int = 6) -> list:
    """Recent headlines as dicts: title, source, date, url (publisher link via Google News)."""
    key = re.sub(r"[^a-z]", "", (source or "").lower())
    q = query + (f" site:{SOURCES[key]}" if key in SOURCES else "") + " when:2d"
    url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"
    items = []
    for e in feedparser.parse(url).entries[:n]:
        title, _, src = e.title.rpartition(" - ")
        items.append({
            "title": title or e.title,
            "source": src or "unknown",
            "date": e.get("published", "")[:16],
            "url": e.get("link"),
        })
    return items


@tool
def financial_news(query: str, source: str = "any") -> str:
    """Latest headlines (last 2 days). source: any, economictimes, financialexpress, businessstandard, mint,
    moneycontrol, groww, reuters, bloomberg, nse, bse. Use for market news, IPOs, company results,
    RBI/money-market, bullion and crude news. Headlines only."""
    items = news_items(query, source)
    lines = [f"- [{i['source']}] {i['title']} ({i['date']})" for i in items]
    return clip("\n".join(lines)) if lines else "No recent headlines found."


# ---------------------------------------------------------------- calculators
OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.USub: operator.neg, ast.Mod: operator.mod,
}
FUNCS = {"sqrt": math.sqrt, "log": math.log, "exp": math.exp, "round": round, "abs": abs}


def _ev(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
        return n.value
    if isinstance(n, ast.BinOp):
        return OPS[type(n.op)](_ev(n.left), _ev(n.right))
    if isinstance(n, ast.UnaryOp):
        return OPS[type(n.op)](_ev(n.operand))
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in FUNCS:
        return FUNCS[n.func.id](*[_ev(a) for a in n.args])
    raise ValueError("unsupported expression")


@tool
def calculator(expression: str) -> str:
    """Evaluate arithmetic, e.g. '10000*((1+0.12/12)**(12*5))', '(2450-2300)/2300*100'. Supports + - * / ** % sqrt log exp round abs."""
    try:
        return str(_ev(ast.parse(expression, mode="eval").body))
    except Exception as e:
        return f"Calc error: {e}"


def finance_calc(kind: str, amount: float, rate_pct: float = 0.0, years: float = 0.0, end_value: float = 0.0) -> str:
    """Deterministic SIP / lump-sum / CAGR / EMI maths, so the LLM never writes formulas."""
    try:
        k, n, r = kind.lower().strip(), round(years * 12), rate_pct / 1200
        if k == "sip":
            fv = amount * (((1 + r) ** n - 1) / r) * (1 + r) if r else amount * n
            inv = amount * n
            return (f"SIP of ₹{amount:,.0f}/month at {rate_pct:g}% p.a. for {years:g} years (monthly compounding, "
                    f"start-of-month): invested ₹{inv:,.0f}; future value ₹{fv:,.0f}; gain ₹{fv - inv:,.0f}")
        if k == "lumpsum":
            fv = amount * (1 + rate_pct / 100) ** years
            return f"Lump sum ₹{amount:,.0f} at {rate_pct:g}% p.a. for {years:g} years: future value ₹{fv:,.0f}"
        if k == "cagr":
            c = ((end_value / amount) ** (1 / years) - 1) * 100
            return f"CAGR {c:.2f}% (from {amount:,.2f} to {end_value:,.2f} over {years:g} years)"
        if k == "emi":
            emi = amount * r * (1 + r) ** n / ((1 + r) ** n - 1) if r else amount / n
            return (f"EMI ₹{emi:,.0f}/month on loan ₹{amount:,.0f} at {rate_pct:g}% for {years:g} years: "
                    f"total payment ₹{emi * n:,.0f}; total interest ₹{emi * n - amount:,.0f}")
        return "Unsupported calculation kind (use sip, lumpsum, cagr or emi)."
    except Exception as e:
        return f"Calculation failed: {e}"


TOOLS = [stock_quote, commodity_price, forex_rate, mutual_fund_nav, financial_news, calculator]
