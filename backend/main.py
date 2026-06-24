"""
TIKT backend — minimal FastAPI skeleton.

Run:
    uvicorn main:app --reload      # serves on http://localhost:8000

TODO: import the existing debate engine (project-root ``main.py``) here to power
the /debate endpoint — e.g. wrap the LangGraph debate / run_round() so a POST to
/debate launches a live multi-agent debate for a given ticker and streams the
result back to the frontend.
"""

import os
import time

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv(dotenv_path="../.env")
FMP_API_KEY = os.getenv("FMP_API_KEY", "")

# Simple in-memory response cache (per-process). Keyed by endpoint + ticker; each
# value is a (data, timestamp) tuple. Cuts repeat calls to the FMP API on reload.
_cache = {}
PRICE_TTL = 120  # 2 minutes — live price, change, market cap
HISTORY_TTL = 3600  # 1 hour — price history chart data
METRICS_TTL = 86400  # 24 hours — margins, ratios, fundamentals
FINANCIALS_TTL = 86400  # 24 hours — income statement, cashflow table
SEARCH_TTL = 3600  # 1 hour — ticker / company search autocomplete


def cache_get(key: str, ttl: int):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < ttl:
            return data
        del _cache[key]
    return None


def cache_set(key: str, data):
    _cache[key] = (data, time.time())


app = FastAPI(title="TIKT API", version="0.1.0")

# Allow the Vite dev server (http://localhost:5173 / :5174) to call the API in dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Placeholder global-index data — mirrors the frontend TickerBar placeholder.
# TODO: replace with live quotes (e.g. yfinance) for the real market feed.
MARKET_DATA = [
    {"name": "S&P 500", "symbol": "^GSPC", "price": "—", "change": "—"},
    {"name": "NASDAQ", "symbol": "^IXIC", "price": "—", "change": "—"},
    {"name": "FTSE 100", "symbol": "^FTSE", "price": "—", "change": "—"},
    {"name": "Nikkei 225", "symbol": "^N225", "price": "—", "change": "—"},
    {"name": "DAX", "symbol": "^GDAXI", "price": "—", "change": "—"},
    {"name": "Hang Seng", "symbol": "^HSI", "price": "—", "change": "—"},
    {"name": "VIX", "symbol": "^VIX", "price": "—", "change": "—"},
    {"name": "10Y Treasury", "symbol": "^TNX", "price": "—", "change": "—"},
]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/market-data")
def market_data():
    return MARKET_DATA


@app.get("/search")
async def search(query: str = ""):
    # FMP's documented /stable/search 404s. The working endpoints are
    # search-symbol (ticker match) and search-name (company-name match); neither
    # alone covers both, so we query both and merge (ticker matches first).
    q = query.strip()
    if len(q) < 1:
        return []

    key = f"search_{q.lower()}"
    cached = cache_get(key, SEARCH_TTL)
    if cached is not None:
        return cached

    import asyncio

    base = "https://financialmodelingprep.com/stable"
    async with httpx.AsyncClient() as client:
        sym_r, name_r = await asyncio.gather(
            client.get(f"{base}/search-symbol", params={"query": q, "limit": 8, "apikey": FMP_API_KEY}, timeout=10),
            client.get(f"{base}/search-name", params={"query": q, "limit": 8, "apikey": FMP_API_KEY}, timeout=10),
        )
    sym_r.raise_for_status()
    name_r.raise_for_status()

    # Dedupe by symbol; symbol (ticker) matches rank ahead of name matches.
    merged = []
    seen = set()
    for row in (sym_r.json() or []) + (name_r.json() or []):
        s = row.get("symbol")
        if not s or s in seen:
            continue
        seen.add(s)
        merged.append({"symbol": s, "name": row.get("name"), "exchange": row.get("exchange")})

    # Float primary US listings to the top before capping. sort() is stable, so
    # relative order within the US and non-US groups is preserved.
    us_exchanges = {"NASDAQ", "NYSE", "AMEX", "NYSE AMERICAN", "NYSE ARCA"}
    merged.sort(key=lambda r: 0 if r.get("exchange") in us_exchanges else 1)
    merged = merged[:8]

    cache_set(key, merged)
    return merged


@app.get("/company/{ticker}/profile")
async def company_profile(ticker: str):
    key = f"profile_{ticker.upper()}"
    cached = cache_get(key, PRICE_TTL)
    if cached is not None:
        return cached

    url = f"https://financialmodelingprep.com/stable/profile?symbol={ticker.upper()}&apikey={FMP_API_KEY}"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    if not data:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Ticker {ticker} not found")
    p = data[0]
    result = {
        "ticker": p.get("symbol"),
        "name": p.get("companyName"),
        "exchange": p.get("exchange"),
        "sector": p.get("sector"),
        "industry": p.get("industry"),
        "price": p.get("price"),
        "change": p.get("change"),
        "change_pct": p.get("changePercentage"),
        "market_cap": p.get("marketCap"),
        "description": (p.get("description") or "")[:500],
        "logo": p.get("image"),
    }
    cache_set(key, result)
    return result


@app.get("/company/{ticker}/price-history")
async def price_history(ticker: str, range: str = "YTD"):
    key = f"price_history_{ticker.upper()}_{range}"
    cached = cache_get(key, HISTORY_TTL)
    if cached is not None:
        return cached

    from datetime import datetime, date
    from dateutil.relativedelta import relativedelta

    today = date.today()

    if range == "1W":
        from_date = today - relativedelta(weeks=1)
    elif range == "1M":
        from_date = today - relativedelta(months=1)
    elif range == "YTD":
        from_date = date(today.year, 1, 1)
    elif range == "1Y":
        from_date = today - relativedelta(years=1)
    elif range == "3Y":
        from_date = today - relativedelta(years=3)
    elif range == "5Y":
        from_date = today - relativedelta(years=5)
    elif range == "10Y":
        from_date = today - relativedelta(years=10)
    else:
        from_date = date(2000, 1, 1)

    url = (
        f"https://financialmodelingprep.com/stable/historical-price-eod/full"
        f"?symbol={ticker.upper()}"
        f"&from={from_date.isoformat()}"
        f"&to={today.isoformat()}"
        f"&apikey={FMP_API_KEY}"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()

    # FMP's /stable/ endpoint returns a flat list of daily bars; the legacy v3
    # API nested them under a "historical" key. Handle both shapes.
    historical = data if isinstance(data, list) else data.get("historical", [])
    historical.sort(key=lambda x: x["date"])

    result = [
        {"date": d["date"], "price": d["close"]}
        for d in historical
    ]
    cache_set(key, result)
    return result


@app.get("/company/{ticker}/metrics")
async def company_metrics(ticker: str):
    key = f"metrics_{ticker.upper()}"
    # Market cap and the price-sensitive multiples are recomputed from the live
    # profile price, so this caches under the short price TTL.
    cached = cache_get(key, PRICE_TTL)
    if cached is not None:
        return cached

    import asyncio

    sym = ticker.upper()
    base = "https://financialmodelingprep.com/stable"
    params = f"symbol={sym}&apikey={FMP_API_KEY}"

    # FMP key-metrics lags to the last reported quarter, so its market cap / EV /
    # P/E / P/S are stale. We instead take the LIVE price from /profile and
    # recompute those multiples ourselves against TTM fundamentals (income +
    # cash-flow statements) and the latest balance sheet.
    async with httpx.AsyncClient() as client:
        prof_r, km_r, inc_r, bs_r, cf_r = await asyncio.gather(
            client.get(f"{base}/profile?{params}", timeout=10),
            client.get(f"{base}/key-metrics?{params}&period=quarter&limit=1", timeout=10),
            client.get(f"{base}/income-statement?{params}&period=quarter&limit=4", timeout=10),
            client.get(f"{base}/balance-sheet-statement?{params}&period=quarter&limit=1", timeout=10),
            client.get(f"{base}/cash-flow-statement?{params}&period=quarter&limit=4", timeout=10),
        )
    for resp in (prof_r, km_r, inc_r, bs_r, cf_r):
        resp.raise_for_status()

    prof_data = prof_r.json()
    if not prof_data:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"No metrics for {ticker}")

    p = prof_data[0]
    km = km_r.json()[0] if km_r.json() else {}
    income = inc_r.json()
    cashflow = cf_r.json()
    b = bs_r.json()[0] if bs_r.json() else {}

    def fmt_large(v):
        if v is None:
            return "N/A"
        if abs(v) >= 1e12:
            return f"${v/1e12:.1f}T"
        if abs(v) >= 1e9:
            return f"${v/1e9:.1f}B"
        if abs(v) >= 1e6:
            return f"${v/1e6:.1f}M"
        return f"${v:,.0f}"

    def fmt_pct(v):
        if v is None:
            return "N/A"
        return f"{v*100:.1f}%"

    def fmt_x(v):
        if v is None:
            return "N/A"
        return f"{v:.1f}x"

    # --- live price + share count ----------------------------------------
    live_price = p.get("price")
    i0 = income[0] if income else {}
    # /stable/profile has no sharesOutstanding; use the latest diluted share
    # count from the income statement, falling back to marketCap / price.
    shares = (
        i0.get("weightedAverageShsOutDil")
        or i0.get("weightedAverageShsOut")
        or ((p.get("marketCap") / live_price) if p.get("marketCap") and live_price else None)
    )
    market_cap_raw = live_price * shares if live_price and shares else None

    # --- TTM aggregates (last 4 quarters) --------------------------------
    ttm_revenue = sum(q.get("revenue") or 0 for q in income) or None
    ttm_eps = sum((q.get("epsDiluted") or q.get("eps") or 0) for q in income) or None
    ttm_fcf = sum(c.get("freeCashFlow") or 0 for c in cashflow) or None
    ttm_sbc = sum(c.get("stockBasedCompensation") or 0 for c in cashflow) or None

    # --- balance-sheet snapshot (most recent quarter) --------------------
    total_debt = b.get("totalDebt")
    cash = b.get("cashAndCashEquivalents")
    equity = b.get("totalStockholdersEquity")

    # --- live, price-sensitive multiples ---------------------------------
    ev = market_cap_raw + (total_debt or 0) - (cash or 0) if market_cap_raw is not None else None
    ev_revenue = ev / ttm_revenue if ev is not None and ttm_revenue else None
    price_to_sales = market_cap_raw / ttm_revenue if market_cap_raw and ttm_revenue else None
    # Trailing P/E off the live price; only meaningful when TTM EPS is positive.
    pe_ratio = live_price / ttm_eps if live_price and ttm_eps and ttm_eps > 0 else None

    # --- margins / ratios (not price-sensitive) --------------------------
    # Gross margin: most recent quarter only (not TTM). FCF margin stays TTM.
    gross_profit = i0.get("grossProfit")
    revenue = i0.get("revenue")
    gross_margin = gross_profit / revenue if gross_profit and revenue else None
    fcf_margin = ttm_fcf / ttm_revenue if ttm_fcf is not None and ttm_revenue else None
    sbc_pct = ttm_sbc / ttm_revenue if ttm_sbc is not None and ttm_revenue else None
    debt_to_equity = total_debt / equity if total_debt is not None and equity else None
    current_ratio = km.get("currentRatio")

    result = {
        "market_cap": fmt_large(market_cap_raw),
        "ev_revenue": fmt_x(ev_revenue),
        "pe_forward": fmt_x(pe_ratio),
        "gross_margin": fmt_pct(gross_margin),
        "fcf_margin": fmt_pct(fcf_margin),
        "sbc_pct": fmt_pct(sbc_pct),
        "price_to_sales": fmt_x(price_to_sales),
        "debt_to_equity": round(debt_to_equity, 2) if debt_to_equity else "N/A",
        "current_ratio": round(current_ratio, 2) if current_ratio else "N/A",
    }
    cache_set(key, result)
    return result


@app.get("/company/{ticker}/financials")
async def company_financials(ticker: str):
    key = f"financials_{ticker.upper()}"
    cached = cache_get(key, FINANCIALS_TTL)
    if cached is not None:
        return cached

    import asyncio

    def fmt(v):
        if v is None:
            return "N/A"
        if abs(v) >= 1e9:
            return f"${v/1e9:.1f}B"
        if abs(v) >= 1e6:
            return f"${v/1e6:.1f}M"
        return f"${v:,.0f}"

    def fmt_pct(v):
        if v is None:
            return "N/A"
        return f"{v*100:.1f}%"

    base = "https://financialmodelingprep.com/stable"
    params = f"symbol={ticker.upper()}&apikey={FMP_API_KEY}"

    async with httpx.AsyncClient() as client:
        inc_q, cf_q, inc_a, cf_a = await asyncio.gather(
            client.get(f"{base}/income-statement?{params}&period=quarter&limit=16", timeout=15),
            client.get(f"{base}/cash-flow-statement?{params}&period=quarter&limit=16", timeout=15),
            client.get(f"{base}/income-statement?{params}&period=annual&limit=5", timeout=15),
            client.get(f"{base}/cash-flow-statement?{params}&period=annual&limit=5", timeout=15),
        )
        for r in (inc_q, cf_q, inc_a, cf_a):
            r.raise_for_status()

    def build_rows(income, cashflow):
        cf_by_date = {c["date"]: c for c in cashflow}
        rows = []
        for i in income:
            date = i.get("date", "")
            cf = cf_by_date.get(date, {})
            rev = i.get("revenue")
            gp = i.get("grossProfit")
            # /stable/ exposes the year as fiscalYear; the legacy v3 API used calendarYear.
            year = i.get("calendarYear") or i.get("fiscalYear") or ""
            rows.append({
                "period": (i.get("period", "") + " " + str(year)).strip(),
                "date": date,
                "revenue": fmt(rev),
                "gross_profit": fmt(gp),
                "gross_margin": fmt_pct(gp / rev if rev and gp else None),
                "operating_income": fmt(i.get("operatingIncome")),
                "net_income": fmt(i.get("netIncome")),
                "fcf": fmt(cf.get("freeCashFlow")),
            })
        return rows

    # Build quarterly rows (8 quarters, newest-first).
    inc_q_data = inc_q.json()
    cf_q_data = cf_q.json()
    quarterly = build_rows(inc_q_data[:8], cf_q_data)

    def safe_sum(values):
        # Skip None; return None only when no valid values exist.
        nums = [v for v in values if v is not None]
        return sum(nums) if nums else None

    cf_by_date_q = {c["date"]: c for c in cf_q_data}

    # Rolling TTM snapshots: 8 windows, each a trailing-twelve-month sum over 4
    # consecutive quarters (window i = quarters i..i+3, newest-first).
    ttm_rows = []
    for i in range(8):
        window = inc_q_data[i:i + 4]
        if len(window) < 4:
            # Not enough quarters left for a full TTM window — skip it.
            continue
        w_revenue = safe_sum(q.get("revenue") for q in window)
        w_gross_profit = safe_sum(q.get("grossProfit") for q in window)
        w_gross_margin = (
            w_gross_profit / w_revenue
            if w_gross_profit is not None and w_revenue
            else None
        )
        w_op_income = safe_sum(q.get("operatingIncome") for q in window)
        w_net_income = safe_sum(q.get("netIncome") for q in window)
        w_fcf = safe_sum(
            cf_by_date_q.get(q.get("date"), {}).get("freeCashFlow") for q in window
        )
        # FMP's period is just "Q1"; append the year to match the row labels.
        head = inc_q_data[i]
        year = head.get("calendarYear") or head.get("fiscalYear") or ""
        label = (head.get("period", "") + " " + str(year)).strip()
        ttm_rows.append({
            "period": label,
            "date": head.get("date", ""),
            "revenue": fmt(w_revenue),
            "gross_profit": fmt(w_gross_profit),
            "gross_margin": fmt_pct(w_gross_margin),
            "operating_income": fmt(w_op_income),
            "net_income": fmt(w_net_income),
            "fcf": fmt(w_fcf),
        })

    # Oldest first so the table reads left-to-right chronologically.
    ttm_rows.reverse()

    # FMP returns both periods newest-first; preserve that order.
    result = {
        "quarterly": quarterly,
        "annual": build_rows(inc_a.json(), cf_a.json()),
        "ttm": ttm_rows,
    }
    cache_set(key, result)
    return result


@app.post("/debate")
def debate():
    return {"message": "not implemented"}
