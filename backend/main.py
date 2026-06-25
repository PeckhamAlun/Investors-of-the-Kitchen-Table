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
import sys
import time
import json
import uuid
import asyncio

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from datetime import datetime, timezone
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi

load_dotenv(dotenv_path="../.env")
FMP_API_KEY = os.getenv("FMP_API_KEY", "")

MONGODB_URI = os.getenv("MONGODB_URI", "")
_mongo_client = None


def get_mongo_db():
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(MONGODB_URI, server_api=ServerApi("1"))
    return _mongo_client[os.getenv("MONGODB_DB_NAME", "kitchen_table")]


def get_company_key_by_ticker(ticker: str) -> str | None:
    # The stored company key is always exact, so resolving it by ticker is far
    # more reliable than normalizing the FMP display name (e.g. "MongoDB, Inc.").
    db = get_mongo_db()
    col = db["company_financials"]
    doc = col.find_one(
        {"ticker": ticker.upper()},
        {"company": 1, "_id": 0}
    )
    return doc.get("company") if doc else None


def _load_debate_engine():
    # The project-root debate engine is ALSO named main.py, so a plain
    # `import main` resolves to THIS backend module (loaded as "main" by uvicorn),
    # not the engine. Load the root file once under a distinct module name.
    if "debate_engine" in sys.modules:
        return sys.modules["debate_engine"]
    import importlib.util

    root_main = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"
    )
    spec = importlib.util.spec_from_file_location("debate_engine", root_main)
    module = importlib.util.module_from_spec(spec)
    sys.modules["debate_engine"] = module
    spec.loader.exec_module(module)
    return module

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
async def market_data():
    key = "market_data"
    cached = cache_get(key, HISTORY_TTL)
    if cached is not None:
        return cached

    symbols = ["^GSPC", "^IXIC", "^FTSE", "^N225",
               "^GDAXI", "^HSI", "^VIX", "^TNX"]
    names = {
        "^GSPC": "S&P 500",
        "^IXIC": "NASDAQ",
        "^FTSE": "FTSE 100",
        "^N225": "Nikkei 225",
        "^GDAXI": "DAX",
        "^HSI": "Hang Seng",
        "^VIX": "VIX",
        "^TNX": "10Y Treasury",
    }

    base = "https://financialmodelingprep.com/stable"

    async def fetch_one(client, sym):
        name = names[sym]
        fallback = {"name": name, "symbol": sym, "price": "—",
                    "change": "—", "change_pct": "", "positive": True}
        try:
            # FMP needs the caret URL-encoded (^GSPC -> %5EGSPC).
            fmp_sym = sym.replace("^", "%5E")
            resp = await client.get(
                f"{base}/quote?symbol={fmp_sym}&apikey={FMP_API_KEY}", timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            if not data:
                return fallback
            q = data[0]
            price = q.get("price")
            change = q.get("change")
            change_pct = q.get("changePercentage")
            if price is None or change is None or change_pct is None:
                return fallback
            # TNX is a yield, quoted to 3 decimals; everything else to 2.
            dp = 3 if sym == "^TNX" else 2
            return {
                "name": name,
                "symbol": sym,
                "price": f"{price:,.{dp}f}",
                "change": f"{'+' if change >= 0 else '-'}{abs(change):,.{dp}f}",
                "change_pct": f"{'+' if change_pct >= 0 else '-'}{abs(change_pct):.2f}%",
                "positive": change >= 0,
            }
        except Exception:
            return fallback

    async with httpx.AsyncClient() as client:
        results = list(await asyncio.gather(*[fetch_one(client, s) for s in symbols]))

    cache_set(key, results)
    return results


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


class DebateRequest(BaseModel):
    ticker: str
    company: str
    agents: list[str] = ["buffett", "cathie_wood", "peter_lynch", "howard_marks", "ray_dalio"]
    turns: int = 1
    topic: str = ""
    session_id: str = ""
    session_history: list[dict] = []
    round_num: int = 1


@app.post("/debate/start")
async def start_debate(req: DebateRequest):
    async def event_stream():
        try:
            # Load the project-root debate engine under a distinct module name so
            # it doesn't clash with this backend's own `main` module. It also
            # re-exports get_latest_ingest_version (imported into its namespace), so
            # we pull it from there and never mutate sys.path here — which would
            # shadow `main` and break `uvicorn main:app`.
            # Loading the root engine runs all of its module-level code
            # (MongoDB + Gemini client init) synchronously, which would block the
            # FastAPI event loop. Run it in a thread pool executor instead.
            import concurrent.futures
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as pool:
                engine = await loop.run_in_executor(pool, _load_debate_engine)
            get_latest_ingest_version = engine.get_latest_ingest_version

            session_id = req.session_id or str(uuid.uuid4())
            topic = req.topic or f"Is {req.ticker} a good investment?"
            ticker = req.ticker.upper()

            # Resolve the company key by ticker — the stored key is always exact.
            company_key = get_company_key_by_ticker(ticker)

            if not company_key:
                # NOT INGESTED → auto-ingest this company's financials by ticker,
                # streaming progress to the UI (the frontend renders ingest_start /
                # ingest_progress / ingest_complete as a "Preparing Research" card),
                # then fall through into the debate. The ingest pipeline lives in
                # scripts/analyse_company.py and is importable here because loading
                # the engine put the project root on sys.path (root main.py line 39).
                from scripts.analyse_company import ingest_by_ticker

                display = req.company or ticker
                yield f"data: {json.dumps({'type': 'ingest_start', 'message': f'Preparing research data for {display} ({ticker})…'})}\n\n"

                # The ingest is synchronous and slow (blocking FMP calls + one Gemini
                # embedding per chunk, minutes total), so it runs in a worker thread.
                # It reports progress back over an asyncio queue via the loop's
                # thread-safe scheduler, so the event loop is never blocked.
                progress_q: asyncio.Queue = asyncio.Queue()

                def _emit(kind, payload):
                    loop.call_soon_threadsafe(progress_q.put_nowait, (kind, payload))

                def _run_ingest():
                    try:
                        result = ingest_by_ticker(
                            ticker,
                            display=display,
                            progress=lambda m: _emit("progress", m),
                        )
                        _emit("result", result)
                    except BaseException as exc:  # incl. SystemExit from FMP helpers
                        _emit("error", str(exc) or exc.__class__.__name__)

                # Default executor (not a `with` pool) so an early return on timeout
                # never blocks on shutdown(wait=True) while the ingest is still running.
                loop.run_in_executor(None, _run_ingest)

                ingest_key = None
                added = 0
                while True:
                    try:
                        kind, payload = await asyncio.wait_for(progress_q.get(), timeout=300)
                    except asyncio.TimeoutError:
                        yield f"data: {json.dumps({'type': 'error', 'message': 'Auto-ingest timed out.'})}\n\n"
                        return
                    if kind == "progress":
                        yield f"data: {json.dumps({'type': 'ingest_progress', 'message': payload})}\n\n"
                        continue
                    if kind == "error":
                        yield f"data: {json.dumps({'type': 'error', 'message': f'Auto-ingest failed: {payload}'})}\n\n"
                        return
                    # kind == "result": (company_key, added, total)
                    ingest_key, added, _total = payload
                    break

                yield f"data: {json.dumps({'type': 'ingest_complete', 'message': f'Research data ready ({added} chunks).'})}\n\n"
                # Re-resolve to the exact stored key (always exact by ticker).
                company_key = get_company_key_by_ticker(ticker) or ingest_key

            # Final safety: confirm the company is now ingested before debating.
            _, version = get_latest_ingest_version(company_key)
            if version == 0:
                yield f"data: {json.dumps({'type': 'error', 'message': f'Company {req.company} could not be ingested.'})}\n\n"
                return

            # Emit session start
            yield f"data: {json.dumps({'type': 'session_start', 'session_id': session_id, 'topic': topic, 'company': company_key, 'agents': req.agents})}\n\n"

            # Stream the debate token-by-token straight from the engine. Each event
            # is piped to the SSE stream verbatim. On round_complete we persist the
            # finished round to MongoDB (exactly as before), then keep streaming.
            # The engine's async generator drives SYNCHRONOUS Anthropic streaming
            # (messages.stream(), not messages.astream()), so iterating it directly
            # on the event loop would block it. Run the generator in a worker thread
            # with its own event loop and bridge its events back over a queue.
            import queue
            import threading

            event_queue = queue.Queue()

            def run_generator():
                try:
                    import asyncio as _asyncio
                    loop = _asyncio.new_event_loop()
                    _asyncio.set_event_loop(loop)

                    async def _collect():
                        async for event in engine.stream_debate_round(
                            topic=topic,
                            company=company_key,
                            agents=req.agents,
                            turns=req.turns,
                            round_num=req.round_num,
                            session_history=req.session_history,
                            audit=False,
                        ):
                            event_queue.put(event)

                    loop.run_until_complete(_collect())
                except Exception as e:
                    event_queue.put({"type": "error", "message": str(e)})
                finally:
                    event_queue.put(None)  # sentinel

            thread = threading.Thread(target=run_generator, daemon=True)
            thread.start()

            while True:
                try:
                    event = event_queue.get(timeout=300)
                except queue.Empty:
                    yield f"data: {json.dumps({'type': 'error', 'message': 'Timeout'})}\n\n"
                    break
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"

                if event.get("type") == "round_complete":
                    db = get_mongo_db()
                    # If this session already exists, refresh it; otherwise create
                    # the document for round 1.
                    existing = db["debates"].find_one({"session_id": session_id})
                    if existing:
                        # event["history"] is the FULL accumulated session history
                        # (the engine seeds each round from session_history), so we
                        # store it directly — concatenating onto the existing doc
                        # would duplicate every prior round.
                        db["debates"].update_one(
                            {"session_id": session_id},
                            {"$set": {
                                "history": event["history"],
                                "rounds": existing.get("rounds", 1) + 1,
                                "updated_at": datetime.now(timezone.utc).isoformat()
                            }}
                        )
                    else:
                        debate_doc = {
                            "_id": session_id,
                            "session_id": session_id,
                            "ticker": req.ticker.upper(),
                            "company": company_key,
                            "topic": req.topic,
                            "agents": req.agents,
                            "turns": req.turns,
                            "history": event["history"],
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "rounds": 1,
                        }
                        db["debates"].replace_one(
                            {"_id": session_id}, debate_doc, upsert=True
                        )

                await asyncio.sleep(0)

            # Generator exhausted — emit the final complete event.
            yield f"data: {json.dumps({'type': 'complete', 'session_id': session_id})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/debate/{session_id}")
async def get_debate(session_id: str):
    db = get_mongo_db()
    doc = db["debates"].find_one({"session_id": session_id})
    if not doc:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Debate not found")
    doc.pop("_id", None)
    return doc


@app.get("/debates")
async def list_all_debates():
    db = get_mongo_db()
    docs = list(db["debates"].find(
        {},
        {
            "session_id": 1,
            "ticker": 1,
            "company": 1,
            "topic": 1,
            "created_at": 1,
            "agents": 1,
            "rounds": 1,
            "_id": 0,
        }
    ).sort("created_at", -1).limit(50))
    return docs


@app.get("/debates/{ticker}")
async def list_debates(ticker: str):
    db = get_mongo_db()
    docs = list(db["debates"].find(
        {"ticker": ticker.upper()},
        {"session_id": 1, "topic": 1, "created_at": 1, "agents": 1, "_id": 0}
    ).sort("created_at", -1).limit(20))
    return docs
