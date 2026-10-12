import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TypedDict
from urllib.parse import quote, quote_plus

from dotenv import load_dotenv
from groq import RateLimitError
from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError, field_validator
from tavily import TavilyClient

from links import links_for
from tools import (COMMODITIES, calculator, clip, commodity_price, finance_calc,
                   forex_rate, mutual_fund_nav, news_items, stock_quote)

load_dotenv()
DEBUG = os.getenv("DEBUG") == "1"


def today() -> str:
    return time.strftime("%A, %d %B %Y")


# ======================================================================= Tavily (budgeted + cached)
tav = TavilyClient(api_key=os.getenv("TAVILY_API_KEY")) if os.getenv("TAVILY_API_KEY") else None
TRUSTED = ["economictimes.indiatimes.com", "financialexpress.com", "business-standard.com",
           "livemint.com", "moneycontrol.com", "groww.in", "reuters.com", "bloomberg.com",
           "nseindia.com", "bseindia.com", "rbi.org.in", "sebi.gov.in", "amfiindia.com"]
_cache, _used, DAILY_CAP = {}, {"day": None, "n": 0}, 25  # counter resets when the app restarts


def _e(kind, text, title=None, url=None):
    """One piece of fetched data. Entries with a url become numbered, citable sources."""
    return {"kind": kind, "text": text, "title": title, "url": url}


def tavily_entries(query: str) -> list:
    if tav is None:
        return [_e("DEEP NEWS", "Deep news search is not configured.")]
    hit = _cache.get(query)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    day = time.strftime("%Y-%m-%d")
    if _used["day"] != day:
        _used.update(day=day, n=0)
    if _used["n"] >= DAILY_CAP:
        return [_e("DEEP NEWS", "Deep-search budget for today is used up.")]
    _used["n"] += 1
    try:
        r = tav.search(query=query, topic="news", days=3, max_results=4,
                       search_depth="basic", include_domains=TRUSTED)
        out = [_e("DEEP NEWS",
                  f"{x['title']} ({(x.get('published_date') or '')[:10]}): {x['content'][:250]}",
                  x["title"], x["url"]) for x in r["results"]]
    except Exception as ex:
        return [_e("DEEP NEWS", f"Deep search failed ({type(ex).__name__}).")]
    _cache[query] = (time.time(), out)
    return out


# ======================================================================= Planner
class Plan(BaseModel):
    stocks: list[str] = Field(default_factory=list)
    commodities: list[str] = Field(default_factory=list)
    fx: list[str] = Field(default_factory=list)
    fx_amount: float = 1.0
    funds: list[str] = Field(default_factory=list)
    news_queries: list[str] = Field(default_factory=list)
    news_source: str = "any"
    deep_news: bool = False
    calc: list[str] = Field(default_factory=list)
    advice_question: bool = False
    fin_kind: str = ""
    fin_amount: float = 0.0
    fin_rate_pct: float = 0.0
    fin_years: float = 0.0
    fin_end_value: float = 0.0

    @field_validator("fx_amount", "fin_amount", "fin_rate_pct", "fin_years", "fin_end_value", mode="before")
    @classmethod
    def _num(cls, v):
        if v is None:
            return 0.0
        if isinstance(v, str):
            return re.sub(r"[^\d.\-]", "", v) or 0.0
        return v

    @field_validator("stocks", "commodities", "fx", "funds", "news_queries", "calc", mode="before")
    @classmethod
    def _lst(cls, v):
        if isinstance(v, list):
            return [str(x) for x in v]
        if isinstance(v, str) and v.strip():
            return [v]
        return []  # 0, "", None, False -> empty list

    @field_validator("news_source", "fin_kind", mode="before")
    @classmethod
    def _str(cls, v):
        return "" if v is None else str(v)


PLAN_SYS = """Decide what data is needed to answer. Fill only relevant fields.
stocks: Yahoo ticker symbols ONLY, never company names. NSE .NS, BSE .BO. Examples: Reliance=RELIANCE.NS, TCS=TCS.NS,
Infosys=INFY.NS, HDFC Bank=HDFCBANK.NS, Nifty 50=^NSEI, Sensex=^BSESN, Bank Nifty=^NSEBANK, S&P 500=^GSPC, Nasdaq=^IXIC, Apple=AAPL.
commodities: gold, silver, crude, brent, natural gas, copper.
fx: like USD/INR; fx_amount = amount to convert (default 1).
funds: scheme names. news_queries: 1-2 short queries (news, IPOs, results, RBI/money market, bullion/crude news).
news_source: any or economictimes, financialexpress, businessstandard, mint, moneycontrol, groww, reuters, bloomberg, nse, bse.
deep_news=true ONLY if the user asks WHY something moved, or wants analysis/outlook.
fin_kind: sip, lumpsum, cagr or emi (leave blank if the question is not about these).
fin_amount: monthly amount for sip; principal for lumpsum/emi; start value for cagr.
fin_rate_pct (annual %), fin_years; fin_end_value only for cagr.
Example: "10,000 monthly SIP at 12% for 10 years" -> fin_kind=sip, fin_amount=10000, fin_rate_pct=12, fin_years=10.
calc: any other arithmetic with literal numbers only, e.g. (2610-2450)/2450*100.
advice_question=true if the user asks what to buy/sell/invest. For such questions ALWAYS also fill stocks
(the named company) and news_queries, so there is data to discuss."""

JSON_RULE = ("\nReply with ONLY one JSON object with exactly these keys: " + ", ".join(Plan.model_fields) + ". "
             "Types: stocks, commodities, fx, funds, news_queries, calc are LISTS of strings "
             "([] when unused, never 0 or \"\"); fx_amount and the fin_* numbers are numbers "
             "(0 when unused, but fx_amount is 1); news_source and fin_kind are strings (\"\" when unused); "
             "deep_news and advice_question are booleans. No prose, no code fences.")


def parse_plan(text: str) -> Plan:
    m = re.search(r"\{.*\}", text, re.S)
    clean = {k: v for k, v in json.loads(m.group(0)).items() if k in Plan.model_fields}
    for _ in range(len(Plan.model_fields)):  # drop any field that fails validation, keep the rest
        try:
            return Plan.model_validate(clean)
        except ValidationError as e:
            for err in e.errors():
                clean.pop(err["loc"][0], None)
    return Plan()


# ======================================================================= Models
MODEL = "openai/gpt-oss-120b"
PLANNER_MODEL = MODEL  # swap to a smaller chat model from your Groq list if you have one


def make_llm(model, max_tokens):
    # gpt-oss is a reasoning model: reasoning tokens count toward max_tokens, so keep effort low
    return ChatGroq(model=model, temperature=0, max_tokens=max_tokens,
                    reasoning_effort="low", max_retries=0)


planner_llm = make_llm(PLANNER_MODEL, 900)
writer = make_llm(MODEL, 1200)
writer_big = make_llm(MODEL, 2000)  # used only if the first answer is cut off


def with_wait(fn, tries=3):
    """Wait out per-minute rate limits instead of crashing."""
    for i in range(tries):
        try:
            return fn()
        except RateLimitError:
            if i == tries - 1:
                raise
            time.sleep(15 * (i + 1))


WRITE_SYS = """You are a markets analyst for Indian and global finance. Use ONLY the DATA provided; the user cannot see it,
so never say "the context", "provided" or "data", and never ask them to supply anything. If something is missing say "I couldn't fetch X".
- Quote numbers exactly as given; never compute or estimate numbers yourself. CALC lines are exact results: present them.
- Cite sources: after each fact taken from a DATA line that starts with a tag like [S3], add that tag, e.g. "closed at 22,520.45 [S1]".
  Use only tags that appear in DATA. Never invent tags or URLs.
- Quotes carry a last-trade date. If it is not today, say it is the last close (markets are shut on weekends and holidays).
- Attribute news to source and date, one bullet per headline, using only that headline's own source. Don't say a headline
  caused a price move unless a source says so. Prefer Reuters, Bloomberg, ET, Mint, Business Standard; label forecasts from lesser-known sites as such.
- Answer only what was asked. Don't add news, "what to watch" lists or notes about unrelated missing data.
- Write numbers with commas (1,234,567), never with spaces.
- For buy/sell questions give no recommendation: state where the price sits in its 52-week range, summarise recent news and what to check.
- If asked for something DATA does not contain (IPO subscription or GMP, company results figures, repo/MIBOR/yield rates,
  MCX or IBJA rates, fundamentals), say live figures aren't available in this app and that the "Explore directly" links under
  the answer open the official source. Do not guess.
- Max 180 words."""


# ======================================================================= Graph state
class S(TypedDict, total=False):
    question: str
    history: list
    plan: Plan
    context: str
    sources: list
    answer: str
    retries: int
    issues: list
    cited: list
    links: list


# ======================================================================= Node 1: plan
def plan_node(s: S):
    msgs = [("system", f"Today is {today()}.\n" + PLAN_SYS + JSON_RULE), *s["history"][-2:], ("user", s["question"])]
    for _ in range(2):
        try:
            resp = with_wait(lambda: planner_llm.invoke(msgs))
            return {"plan": parse_plan(resp.content)}
        except Exception as e:
            if DEBUG:
                print("PLANNER ERROR:", repr(e)[:300])
    return {"plan": Plan(news_queries=[s["question"]])}


# ======================================================================= Node 2: fetch (no LLM)
def _quote(x):
    t = stock_quote.invoke({"symbol": x})
    m = re.match(r"^([^\s:]+): ", t)
    sym = m.group(1) if m else None
    url = f"https://finance.yahoo.com/quote/{quote(sym, safe='')}/" if sym else None
    return [_e("QUOTE", t, f"Yahoo Finance: {sym}" if sym else None, url)]


def _commodity(x):
    t = commodity_price.invoke({"name": x})
    sym = COMMODITIES.get(x.lower().strip())
    url = f"https://finance.yahoo.com/quote/{quote(sym, safe='')}/" if sym else None
    return [_e("COMMODITY", t, f"Yahoo Finance: {sym} futures" if sym else None, url)]


def _fx(x, amount):
    base, q = x.split("/")[0].strip().upper(), x.split("/")[-1].strip().upper()
    t = forex_rate.invoke({"base": base, "quote": q, "amount": amount})
    url = f"https://api.frankfurter.dev/v1/latest?base={base}&symbols={q}&amount={amount}"
    return [_e("FX", t, "Frankfurter · ECB reference rates", url)]


def _fund(x):
    t = mutual_fund_nav.invoke({"name": x})
    return [_e("MF", t, "mfapi.in · AMFI NAV data", f"https://api.mfapi.in/mf/search?q={quote_plus(x)}")]


def _news(q, src):
    items = news_items(q, src)
    out = [_e("NEWS", f"{i['source']}: {i['title']} ({i['date']})", f"{i['source']}: {i['title']}", i["url"])
           for i in items]
    return out or [_e("NEWS", "No recent headlines found.")]


def fetch_node(s: S):
    p, jobs = s["plan"], []
    src = p.news_source or "any"
    for x in p.stocks[:4]:
        jobs.append(lambda x=x: _quote(x))
    for x in p.commodities[:3]:
        jobs.append(lambda x=x: _commodity(x))
    for x in p.fx[:2]:
        if "/" in x:
            jobs.append(lambda x=x: _fx(x, p.fx_amount))
    for x in p.funds[:2]:
        jobs.append(lambda x=x: _fund(x))
    for e in p.calc[:2]:
        jobs.append(lambda e=e: [_e("CALC", f"{e} = {calculator.invoke({'expression': e})}")])
    if p.fin_kind and p.fin_amount:
        jobs.append(lambda: [_e("CALC", finance_calc(p.fin_kind, p.fin_amount, p.fin_rate_pct,
                                                     p.fin_years, p.fin_end_value))])
    for q in p.news_queries[:2]:
        jobs.append(lambda q=q: _news(q, src))
    if p.deep_news and p.news_queries:
        jobs.append(lambda: tavily_entries(p.news_queries[0]))

    def _run(job):
        try:
            return job()
        except Exception as ex:
            return [_e("ERROR", f"a data source was unavailable ({type(ex).__name__})")]

    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(_run, jobs))

    lines, sources, seen = [], [], set()
    for entries in results:
        for en in entries:
            if en["url"]:
                if en["url"] in seen:  # same headline returned by two searches
                    continue
                seen.add(en["url"])
                sid = len(sources) + 1
                sources.append({"id": sid, "title": en["title"], "url": en["url"]})
                lines.append(f"[S{sid}] {en['kind']}: {en['text']}")
            else:
                lines.append(f"{en['kind']}: {en['text']}")
    return {"context": clip("\n".join(lines), 3500), "sources": sources}


# ======================================================================= Node 3: write
def write_node(s: S):
    extra = (f"\nYour previous answer contained numbers not in the data: {s['issues']}. Remove or fix them."
             if s.get("issues") else "")
    msgs = [("system", f"Today is {today()}.\n" + WRITE_SYS + extra), *s["history"][-2:],
            ("user", f"DATA:\n{s['context']}\n\nQUESTION: {s['question']}")]
    resp = with_wait(lambda: writer.invoke(msgs))
    if DEBUG:
        print("WRITER:", resp.response_metadata.get("finish_reason"), getattr(resp, "usage_metadata", None))
    if resp.response_metadata.get("finish_reason") == "length" or len(resp.content.strip()) < 15:
        resp = with_wait(lambda: writer_big.invoke(msgs))  # one retry with a bigger budget
    return {"answer": resp.content}


# ======================================================================= Node 4: check + cite
NUM = re.compile(r"\d[\d,]*\.?\d*")
GROUPED = re.compile(r"(?<!\d)(\d{1,3})((?:[ \u202f\u00a0\u2009]\d{3})+)(?!\d)")
SCALES = (1, 1e3, 1e5, 1e6, 1e7, 1e9)  # thousand, lakh, million, crore, billion
TAG = re.compile(r"\[S(\d+)\]")
TAG_GROUP = re.compile(r"\[(S\d+(?:\s*,\s*S\d+)+)\]")


def _vals(t):
    t = GROUPED.sub(lambda m: m.group(1) + re.sub(r"\D", "", m.group(2)), t)
    out = set()
    for n in NUM.findall(t):
        try:
            out.add(float(n.replace(",", "").rstrip(".")))
        except ValueError:
            pass
    return out


def _supported(a, allowed):
    return any(abs(a * k - c) <= 0.005 * abs(c) for c in allowed for k in SCALES)


def link_citations(answer: str, sources: list):
    """Turn [S3] tags into numbered markdown links [1](url) and return the cited sources in order."""
    answer = TAG_GROUP.sub(lambda m: "".join(f"[{t.strip()}]" for t in m.group(1).split(",")), answer)
    by_id = {s["id"]: s for s in sources}
    order = []
    for m in TAG.finditer(answer):
        i = int(m.group(1))
        if i in by_id and i not in order:
            order.append(i)
    num = {i: n for n, i in enumerate(order, 1)}

    def rep(m):
        i = int(m.group(1))
        return f"[[{num[i]}]]({by_id[i]['url']})" if i in num else ""

    answer = TAG.sub(rep, answer)
    answer = re.sub(r"[ \t]+([.,;:])", r"\1", answer)  # tidy spaces left by removed tags
    answer = re.sub(r"(?<=\S)[ ]{2,}", " ", answer)
    cited = [dict(by_id[i], n=num[i]) for i in order]
    if not cited:  # the writer cited nothing: still show what was consulted
        cited = [dict(s, n=k) for k, s in enumerate(sources[:6], 1)]
    return answer, cited


def check_node(s: S):  # rule-based evaluator, zero tokens
    allowed = _vals(s["context"]) | _vals(s["question"]) | _vals(" ".join(m[1] for m in s["history"][-2:]))
    bad = [a for a in _vals(s["answer"])
           if not (a == int(a) and (a < 100 or 1900 <= a <= 2100)) and not _supported(a, allowed)]
    r = s.get("retries", 0)
    if bad and r < 1:
        return {"issues": bad, "retries": r + 1}  # one retry only
    ans, cited = link_citations(s["answer"], s.get("sources", []))
    if bad:
        ans += "\n\n⚠️ Some figures could not be verified against the fetched data."
    if s["plan"].advice_question:
        ans += "\n\n_Information only, not investment advice._"
    return {"issues": [], "answer": ans, "cited": cited, "links": links_for(s["plan"], s["question"])}


# ======================================================================= Wire the graph
g = StateGraph(S)
g.add_node("plan", plan_node)
g.add_node("fetch", fetch_node)
g.add_node("write", write_node)
g.add_node("check", check_node)
g.add_edge(START, "plan")
g.add_edge("plan", "fetch")
g.add_edge("fetch", "write")
g.add_edge("write", "check")
g.add_conditional_edges("check", lambda s: "write" if s.get("issues") else END, ["write", END])
app = g.compile()


if __name__ == "__main__":
    history = []
    while True:
        q = input("\nYou: ").strip()
        if q.lower() in ("exit", "quit"):
            break
        out = app.invoke({"question": q, "history": history, "retries": 0})
        if DEBUG:
            print("PLAN:", out["plan"].model_dump())
            print("CONTEXT:\n", out["context"])
        print("Bot:", out["answer"])
        for s_ in out.get("cited", []):
            print(f"  [{s_['n']}] {s_['title']} - {s_['url']}")
        for label, url in out.get("links", []):
            print(f"  link: {label} - {url}")
        history += [("user", q), ("assistant", out["answer"])]
