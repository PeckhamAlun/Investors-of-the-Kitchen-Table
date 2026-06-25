"""
==============================================================================
  ANALYSE COMPANY — AUTO INGESTION via FMP
  (MongoDB Atlas + Gemini embeddings edition)
==============================================================================

  Pulls a company's financials automatically (no manual PDF dropping) and
  loads them into the shared `company_financials` MongoDB Atlas collection so
  the debate engine can cite real numbers.

  Data layers, all written as clean human-readable text chunks:
    1. FMP financials      — income / balance / cash-flow, one quarter per chunk
    2. FMP metrics         — valuation/margin snapshot + analyst recommendations
    3. Computed metrics    — SBC %, FCF margin, YoY growth, gross-margin, Rule of 40
    4. FMP transcripts     — last 12 quarters of earnings-call transcripts

  Writes to the MongoDB Atlas `company_financials` collection that main.py
  reads from, embedding each chunk with Gemini (the same model/vector space the
  debate engine queries against). Wipe-by-default; --append keeps existing data.

  USAGE
  -----
    python scripts/analyse_company.py --ticker MDB
    python scripts/analyse_company.py --ticker MDB --exchange NASDAQ
    python scripts/analyse_company.py --ticker MDB --append
    python scripts/analyse_company.py --audit MongoDB

  DEPENDENCIES
  ------------
    py -3.11 -m pip install requests pymongo google-genai
    (pymongo + google-genai already present from main.py's Atlas migration)

  Requires MONGODB_URI, GOOGLE_API_KEY and FMP_API_KEY in .env (same as main.py).

  NOTE ON COMPANY NAMING
  ----------------------
  The stored `company` field is routed through config.normalize_company() —
  the SAME function main.py uses on --company — so retrieval always matches.
  Known camel-case names (MongoDB, CrowdStrike, ...) are preserved via the
  override table in config.py.
==============================================================================
"""

import os
import re
import sys
import time
import argparse
from datetime import datetime, timezone

# Force UTF-8 stdout/stderr so the ═ box-drawing chars and emoji in the banners
# don't raise UnicodeEncodeError on a Windows cp1252 console (CLAUDE.md §11).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# (yfinance has been fully removed — all financial data now comes from FMP.
# See pull_fmp_financials() and pull_fmp_transcripts() below.)

# ── third-party (always needed: requests for FMP API calls; pymongo for all DB access) ──
try:
    import requests
except ImportError:
    sys.exit("ERROR: 'requests' is not installed.  Run:  py -3.11 -m pip install requests")

try:
    from pymongo.mongo_client import MongoClient
    from pymongo.server_api import ServerApi
    from pymongo.mongo_client import MongoClient
    from pymongo.server_api import ServerApi
except ImportError:
    sys.exit("ERROR: 'pymongo' is not installed.  Run:  py -3.11 -m pip install pymongo")
    sys.exit("ERROR: 'pymongo' is not installed.  Run:  py -3.11 -m pip install pymongo")

# NOTE: the Gemini client (google-genai) is imported lazily inside ingest() so
# --audit and --list never load it — they only read MongoDB.

# ── project config (single source of truth) ──
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    normalize_company,
    MONGODB_URI,
    MONGODB_DB_NAME,
    GEMINI_EMBED_MODEL,
    GOOGLE_API_KEY,
    COMPANY_COLLECTION,
    FMP_API_KEY,
)

# ── MongoDB Atlas connection (single client, reused across modes) ──
_mongo_client = None

def get_db():
    """Return the Atlas database handle, opening the client on first use."""
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(MONGODB_URI, server_api=ServerApi("1"))
    return _mongo_client[MONGODB_DB_NAME]

# ==============================================================================
# CONSTANTS
# ==============================================================================

# Gemini batch-embedding tuning (avoid rate limits)
EMBED_BATCH_SIZE = 50    # chunks per Gemini embedding call
BATCH_SLEEP_SECS = 0.5   # pause between batches


# ==============================================================================
# SMALL FORMATTING HELPERS
# ==============================================================================

def fnum(v):
    """Safe float, or None. Treats NaN (incl. pandas NaN) as None."""
    try:
        if v is None or v == "":
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def money(v):
    """Human-readable money: -$32.1M, $1.2B, $529.4M, $940."""
    v = fnum(v)
    if v is None:
        return "N/A"
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e9:
        return f"{sign}${a/1e9:.1f}B"
    if a >= 1e6:
        return f"{sign}${a/1e6:.1f}M"
    if a >= 1e3:
        return f"{sign}${a/1e3:.1f}K"
    return f"{sign}${a:,.0f}"


def pct(ratio, signed=False):
    """ratio 0.758 -> '75.8%'.  signed=True -> '+22%'."""
    r = fnum(ratio)
    if r is None:
        return "N/A"
    if signed:
        return f"{r*100:+.0f}%"
    return f"{r*100:.1f}%"


# ==============================================================================
# FMP (FINANCIAL MODELING PREP) DATA HELPERS
# ==============================================================================

FMP_BASE = "https://financialmodelingprep.com/stable"


def _fmp_get(endpoint, **params):
    """GET an FMP /stable endpoint and return parsed JSON (list or dict), or None
    on any network/HTTP/parse error. The api key is added automatically."""
    params["apikey"] = FMP_API_KEY
    url = f"{FMP_BASE}/{endpoint}"
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"   ⚠️  FMP '{endpoint}' request failed ({e}).")
        return None


def _full_year_quarters(stmts):
    """Given FMP quarterly statements (any order), return (fiscal_year_str,
    [the 4 quarters of the most recent COMPLETE fiscal year, oldest->newest]).
    Returns (None, []) if no fiscal year has all four quarters present."""
    by_year = {}
    for s in stmts or []:
        fy = s.get("fiscalYear") or s.get("calendarYear")
        if fy is None:
            continue
        by_year.setdefault(str(fy), []).append(s)
    for fy in sorted(by_year, reverse=True):
        qs = [q for q in by_year[fy] if str(q.get("period") or "").upper().startswith("Q")]
        if len(qs) >= 4:
            return fy, sorted(qs, key=lambda s: s.get("date", ""))[-4:]
    return None, []


def _sum_field(rows, field):
    """Sum one numeric field across rows, ignoring missing/None. None if no value."""
    vals = [fnum(r.get(field)) for r in (rows or [])]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


# ==============================================================================
# STEP 1 — TICKER VALIDATION / RESOLUTION (FMP /profile)
# ==============================================================================

def resolve_ticker(ticker, exchange):
    """Validate the ticker via the FMP /profile endpoint and confirm with the
    user. --exchange (if given) overrides the displayed exchange only. Exits if
    FMP has no profile for the symbol. Returns dict:
    {name, ticker, exchange, sector, industry}."""
    ticker = ticker.upper()
    if not FMP_API_KEY:
        sys.exit("ERROR: FMP_API_KEY is not set (.env). Cannot validate ticker.")

    data = _fmp_get("profile", symbol=ticker)
    if not data or not isinstance(data, list):
        print(f"\n  ERROR: ticker '{ticker}' not found on FMP (empty profile).")
        print("  Check the symbol and try again.")
        sys.exit(1)

    p        = data[0] or {}
    name     = p.get("companyName") or ticker
    sector   = p.get("sector")
    industry = p.get("industry")
    exch     = exchange.upper() if exchange else (p.get("exchangeShortName") or p.get("exchange") or "UNKNOWN")

    print(f"\n  🔍 Found: {name} ({p.get('symbol', ticker)}) — {p.get('exchangeShortName') or exch}")
    if sector or industry:
        print(f"     Sector: {sector or '?'} | Industry: {industry or '?'}")
    confirm = input("     Confirm ingestion? [Y/n]: ").strip().lower()
    if confirm in ("n", "no"):
        print("  Cancelled.")
        sys.exit(0)

    return {"name": name, "ticker": ticker, "exchange": exch,
            "sector": sector, "industry": industry}


# ==============================================================================
# STEP 2 — FMP DATA PULL  (profile / income / balance / cash flow / metrics / analyst)
# ==============================================================================

def pull_fmp_financials(ticker):
    """Pull all of a company's financials from FMP and return them as a dict:
    {profile, income, balance, cashflow, key_metrics, analyst} — each the parsed
    JSON list/dict from FMP (income/balance/cashflow are quarterly, newest-first).
    Exits if the key is missing or the profile is empty; an individual statement
    endpoint failing is non-fatal (logged, left as an empty list)."""
    if not FMP_API_KEY:
        sys.exit("ERROR: FMP_API_KEY is not set (.env). Cannot pull financials.")

    print("\n  ── FMP data pull ──")

    print("  📊 Pulling company profile...  ", end="", flush=True)
    profile = _fmp_get("profile", symbol=ticker)
    if not profile or not isinstance(profile, list):
        sys.exit(f"\n  ERROR: FMP returned no profile for '{ticker}'. Check the symbol.")
    print("   ✅ 1 record")
    time.sleep(0.2)

    print("  📊 Pulling income statements...", end="", flush=True)
    income = _fmp_get("income-statement", symbol=ticker, period="quarter", limit=16) or []
    print(f"   ✅ {len(income)} quarters")
    time.sleep(0.2)

    print("  📊 Pulling balance sheets...   ", end="", flush=True)
    balance = _fmp_get("balance-sheet-statement", symbol=ticker, period="quarter", limit=16) or []
    print(f"   ✅ {len(balance)} quarters")
    time.sleep(0.2)

    print("  📊 Pulling cash flow...        ", end="", flush=True)
    cashflow = _fmp_get("cash-flow-statement", symbol=ticker, period="quarter", limit=16) or []
    print(f"   ✅ {len(cashflow)} quarters")
    time.sleep(0.2)

    print("  📊 Pulling key metrics...      ", end="", flush=True)
    key_metrics = _fmp_get("key-metrics", symbol=ticker, period="quarter", limit=4) or []
    print(f"   ✅ {len(key_metrics)} rows")
    time.sleep(0.2)

    print("  📊 Pulling analyst consensus...", end="", flush=True)
    analyst = _fmp_get("grades-summary", symbol=ticker) or []
    print(f"   ✅ {len(analyst)} rows")

    return {"profile": profile, "income": income, "balance": balance,
            "cashflow": cashflow, "key_metrics": key_metrics, "analyst": analyst}


# ==============================================================================
# STEP 2b — MARKET DATA CHUNK BUILDING (from FMP)
# ==============================================================================

def build_market_chunks(name, ticker, fmp):
    """Convert FMP financial data into text chunks.
    Returns a list of dicts: {text, source, source_type, period, form_type}.

    Emits one ANNUAL summary chunk each (income / balance / cash flow, most recent
    COMPLETE fiscal year) labelled 'FY2026 Annual', plus the 4 most recent
    QUARTERS each, labelled 'Q1 FY2026' etc. (descending), plus profile, key
    metrics and analyst-recommendation chunks. The text format is unchanged from
    the previous build so downstream parsing in main.py still matches. The
    source_type tags are kept as 'yfinance_financials' / 'yfinance_metrics' for
    backward compatibility with main.py's snapshot parser (do NOT rename them)."""
    chunks = []
    income   = fmp.get("income") or []
    balance  = fmp.get("balance") or []
    cashflow = fmp.get("cashflow") or []
    profile  = (fmp.get("profile") or [{}])[0] or {}
    km_list  = fmp.get("key_metrics") or []
    km       = (km_list[0] if km_list else {}) or {}
    analyst  = fmp.get("analyst") or []

    inc_by_date = {r.get("date"): r for r in income}
    cf_by_date  = {r.get("date"): r for r in cashflow}

    # ---- shared text builders (close over name / ticker) ----
    def income_text(row, sbc, prior_rev, period, lab):
        rev = fnum(row.get("revenue"))
        gp  = fnum(row.get("grossProfit"))
        op  = fnum(row.get("operatingIncome"))
        ni  = fnum(row.get("netIncome"))
        if rev is None and gp is None and op is None and ni is None:
            return None
        yoy = ""
        if rev and prior_rev:
            yoy = f" ({pct((rev - prior_rev) / prior_rev, signed=True)} YoY)"
        gm = (gp / rev) if (gp is not None and rev) else None
        if sbc is not None:
            sbc_pct  = f" ({pct(sbc / rev)} of revenue)" if rev else ""
            sbc_line = f"Stock-Based Compensation: {money(sbc)}{sbc_pct}"
        else:
            sbc_line = "Stock-Based Compensation: N/A"
        rnd = fnum(row.get("researchAndDevelopmentExpenses"))
        sga = fnum(row.get("sellingGeneralAndAdministrativeExpenses"))
        return (
            f"{name} ({ticker}) — Income Statement {lab}\n"
            f"Period: {period} | Source: FMP\n\n"
            f"Revenue: {money(rev)}{yoy}\n"
            f"Gross Profit: {money(gp)} (Gross Margin: {pct(gm)})\n"
            f"Operating Income: {money(op)}\n"
            f"Net Income: {money(ni)}\n"
            f"{sbc_line}\n"
            f"R&D Expense: {money(rnd)}\n"
            f"Selling, General & Admin: {money(sga)}"
        )

    def balance_text(row, period, lab):
        return (
            f"{name} ({ticker}) — Balance Sheet {lab}\n"
            f"Period: {period} | Source: FMP\n\n"
            f"Cash & Equivalents: {money(row.get('cashAndCashEquivalents'))}\n"
            f"Cash, Equiv. & Short-Term Investments: {money(row.get('cashAndShortTermInvestments'))}\n"
            f"Total Current Assets: {money(row.get('totalCurrentAssets'))}\n"
            f"Total Assets: {money(row.get('totalAssets'))}\n"
            f"Total Current Liabilities: {money(row.get('totalCurrentLiabilities'))}\n"
            f"Total Debt: {money(row.get('totalDebt'))}\n"
            f"Total Liabilities: {money(row.get('totalLiabilities'))}\n"
            f"Total Stockholders' Equity: {money(row.get('totalStockholdersEquity'))}"
        )

    def cashflow_text(row, rev_row, period, lab):
        ocf = fnum(row.get("operatingCashFlow"))
        fcf = fnum(row.get("freeCashFlow"))
        fcf_margin = f" (FCF Margin: {pct(fcf / rev_row)})" if (fcf is not None and rev_row) else ""
        return (
            f"{name} ({ticker}) — Cash Flow {lab}\n"
            f"Period: {period} | Source: FMP\n\n"
            f"Operating Cash Flow: {money(ocf)}\n"
            f"Capital Expenditure: {money(row.get('capitalExpenditure'))}\n"
            f"Free Cash Flow: {money(fcf)}{fcf_margin}\n"
            f"Stock-Based Compensation: {money(row.get('stockBasedCompensation'))}\n"
            f"Change in Cash: {money(row.get('netChangeInCash'))}"
        )

    def add(text, source, period, form_type, source_type="yfinance_financials"):
        if text is None:
            return
        chunks.append({"text": text, "source": source, "source_type": source_type,
                       "period": period, "form_type": form_type})

    def q_label(row):
        per = str(row.get("period") or "").upper()
        fy  = row.get("fiscalYear") or row.get("calendarYear") or (row.get("date", "")[:4] or "?")
        return f"{per} FY{fy}" if per.startswith("Q") else f"FY{fy}"

    # ---- ANNUAL: most recent COMPLETE fiscal year — one summary chunk each ----
    fy, yr_q = _full_year_quarters(income)
    if fy and len(yr_q) == 4:
        period  = yr_q[-1].get("date", "")        # fiscal year-end date
        lab     = f"FY{fy} Annual"
        ann_inc = {f: _sum_field(yr_q, f) for f in
                   ("revenue", "grossProfit", "operatingIncome", "netIncome",
                    "researchAndDevelopmentExpenses", "sellingGeneralAndAdministrativeExpenses")}
        ann_sbc = _sum_field([cf_by_date.get(q.get("date"), {}) for q in yr_q],
                             "stockBasedCompensation")
        prior_income = [r for r in income
                        if str(r.get("fiscalYear") or r.get("calendarYear")) != str(fy)]
        pfy, pyr_q   = _full_year_quarters(prior_income)
        prior_rev    = _sum_field(pyr_q, "revenue") if pfy else None
        add(income_text(ann_inc, ann_sbc, prior_rev, period, lab),
            f"{ticker} Income Statement {lab}", period, "income_statement")

    fyb, yr_b = _full_year_quarters(balance)
    if fyb and yr_b:
        row    = yr_b[-1]                          # fiscal year-end balance (point-in-time)
        period = row.get("date", ""); lab = f"FY{fyb} Annual"
        add(balance_text(row, period, lab), f"{ticker} Balance Sheet {lab}", period, "balance_sheet")

    fyc, yr_c = _full_year_quarters(cashflow)
    if fyc and len(yr_c) == 4:
        period = yr_c[-1].get("date", ""); lab = f"FY{fyc} Annual"
        ann_cf = {f: _sum_field(yr_c, f) for f in
                  ("operatingCashFlow", "capitalExpenditure", "freeCashFlow",
                   "stockBasedCompensation", "netChangeInCash")}
        rev_row = _sum_field([inc_by_date.get(q.get("date"), {}) for q in yr_c], "revenue")
        add(cashflow_text(ann_cf, rev_row, period, lab),
            f"{ticker} Cash Flow {lab}", period, "cash_flow")

    # ---- QUARTERLY: 4 most recent quarters, individually (newest-first) ----
    for i, row in enumerate(income[:4]):
        period    = row.get("date", "")
        lab       = q_label(row)
        sbc       = fnum(cf_by_date.get(period, {}).get("stockBasedCompensation"))
        prior_rev = fnum(income[i + 4].get("revenue")) if i + 4 < len(income) else None
        t = income_text(row, sbc, prior_rev, period, lab)
        if t is None:
            print(f"  ⚠️  Skipping {period} income statement — all fields N/A (data not yet available)")
            continue
        add(t, f"{ticker} Income Statement {lab}", period, "income_statement")

    for row in balance[:4]:
        period = row.get("date", ""); lab = q_label(row)
        add(balance_text(row, period, lab), f"{ticker} Balance Sheet {lab}", period, "balance_sheet")

    for row in cashflow[:4]:
        period  = row.get("date", ""); lab = q_label(row)
        rev_row = fnum(inc_by_date.get(period, {}).get("revenue"))
        add(cashflow_text(row, rev_row, period, lab), f"{ticker} Cash Flow {lab}", period, "cash_flow")

    # ---- Profile (single chunk) ----
    if profile:
        desc = (profile.get("description") or "")[:1500]
        text = (
            f"{name} ({ticker}) — Company Profile\n"
            f"Source: FMP\n\n"
            f"Exchange: {profile.get('exchangeShortName') or profile.get('exchange') or '?'}\n"
            f"Sector: {profile.get('sector', '?')} | Industry: {profile.get('industry', '?')}\n"
            f"Market Cap: {money(profile.get('marketCap'))}\n"
            f"Price: {money(profile.get('price'))} | Beta: {profile.get('beta', '?')}\n"
            f"Employees: {profile.get('fullTimeEmployees', '?')} | Country: {profile.get('country', '?')}\n"
            f"Website: {profile.get('website', '?')}\n\n"
            f"Description: {desc}"
        )
        chunks.append({"text": text, "source": f"{ticker} Company Profile",
                       "source_type": "yfinance_financials", "period": "current",
                       "form_type": "profile"})

    # ---- Key metrics snapshot ----
    def raw(d, k):
        v = d.get(k)
        return "N/A" if v in (None, "") else v

    latest_bal = balance[0] if balance else {}
    fcf_ttm    = _sum_field(cashflow[:4], "freeCashFlow")
    text = (
        f"{name} ({ticker}) — Key Metrics & Ratios (current snapshot)\n"
        f"Source: FMP\n\n"
        f"Market Cap: {money(profile.get('marketCap'))}\n"
        f"Enterprise Value: {money(km.get('enterpriseValue'))}\n"
        f"Trailing P/E: {raw(km, 'peRatio')}\n"
        f"Forward P/E: N/A\n"
        f"Price/Sales (TTM): {raw(km, 'priceToSalesRatio')}\n"
        f"EV/Revenue: {raw(km, 'evToRevenue')}\n"
        f"EV/EBITDA: {raw(km, 'evToEbitda')}\n"
        f"Gross Margins: {pct(km.get('grossProfitMargin'))}\n"
        f"Operating Margins: {pct(km.get('operatingProfitMargin'))}\n"
        f"Profit Margins: {pct(km.get('netProfitMargin'))}\n"
        f"Return on Equity: {pct(km.get('returnOnEquity'))}\n"
        f"Revenue Growth (YoY): {pct(km.get('revenueGrowth'), signed=True)}\n"
        f"Total Cash: {money(latest_bal.get('cashAndCashEquivalents'))} | Total Debt: {money(latest_bal.get('totalDebt'))}\n"
        f"Debt/Equity: {raw(km, 'debtToEquity')} | Current Ratio: {raw(km, 'currentRatio')}\n"
        f"Free Cash Flow (TTM): {money(fcf_ttm)}"
    )
    chunks.append({"text": text, "source": f"{ticker} Key Metrics",
                   "source_type": "yfinance_metrics", "period": "current",
                   "form_type": "key_metrics"})

    # ---- Analyst recommendations (current consensus from FMP) ----
    if analyst:
        a     = analyst[0] or {}
        sb, b = int(fnum(a.get("strongBuy")) or 0), int(fnum(a.get("buy")) or 0)
        h     = int(fnum(a.get("hold")) or 0)
        s, ss = int(fnum(a.get("sell")) or 0), int(fnum(a.get("strongSell")) or 0)
        text = (
            f"{name} ({ticker}) — Analyst Recommendations\n"
            f"Source: FMP\n\n"
            f"Consensus: {a.get('consensus', 'N/A')}\n"
            f"strongBuy buy hold sell strongSell\n"
            f"{sb} {b} {h} {s} {ss}"
        )
        chunks.append({"text": text, "source": f"{ticker} Analyst Recommendations",
                       "source_type": "yfinance_metrics", "period": "current",
                       "form_type": "analyst_recommendations"})

    return chunks


# ==============================================================================
# STEP 3 — COMPUTED METRICS
# ==============================================================================

def build_computed_chunk(name, ticker, fmp):
    """Compute headline trends over the trailing 4 quarters -> one chunk."""
    income   = fmp.get("income") or []
    cashflow = fmp.get("cashflow") or []
    if not income:
        return None
    cf_by_date = {r.get("date"): r for r in cashflow}

    today = datetime.now().strftime("%Y-%m-%d")
    sbc_lines, fcf_lines, growth_lines, gm_lines, rule40_lines = [], [], [], [], []

    for i, row in enumerate(income[:4]):
        date = row.get("date", "")
        rev  = fnum(row.get("revenue"))
        cf   = cf_by_date.get(date, {})
        sbc  = fnum(cf.get("stockBasedCompensation"))
        fcf  = fnum(cf.get("freeCashFlow"))

        if rev and sbc is not None:
            sbc_lines.append(f"  {date}: {pct(sbc / rev)}")

        fcf_margin = None
        if rev and fcf is not None:
            fcf_margin = fcf / rev
            fcf_lines.append(f"  {date}: {pct(fcf_margin)}")

        growth    = None
        prior_rev = fnum(income[i + 4].get("revenue")) if i + 4 < len(income) else None
        if rev and prior_rev:
            growth = (rev - prior_rev) / prior_rev
            growth_lines.append(f"  {date}: {pct(growth, signed=True)}")

        gp = fnum(row.get("grossProfit"))
        gm = (gp / rev) if (gp is not None and rev) else None
        if gm is not None:
            gm_lines.append(f"  {date}: {pct(gm)}")

        if growth is not None and fcf_margin is not None:
            rule40_lines.append(f"  {date}: {growth * 100 + fcf_margin * 100:.1f}")

    def block(title, lines):
        if not lines:
            return f"{title}:\n  (insufficient data)\n"
        return f"{title}:\n" + "\n".join(lines) + "\n"

    text = (
        f"{name} ({ticker}) — Computed Metrics Summary\n"
        f"Generated: {today} | Source: computed from FMP data\n\n"
        + block("SBC as % of Revenue (trailing 4 quarters)", sbc_lines) + "\n"
        + block("FCF Margin (reported)", fcf_lines) + "\n"
        + block("Revenue Growth YoY (quarterly)", growth_lines) + "\n"
        + block("Gross Margin Trend", gm_lines) + "\n"
        + block("Rule of 40 (Revenue Growth % + FCF Margin %)", rule40_lines)
    ).rstrip()

    most_recent = income[0].get("date", "")
    return {"text": text, "source": f"{ticker} Computed Metrics",
            "source_type": "computed_metrics", "period": most_recent,
            "form_type": "computed_metrics"}


# ==============================================================================
# STEP 3a — ROLLING TTM SUMMARY
# ==============================================================================

def compute_ttm_chunk(name, ticker, fmp):
    """Build one chunk summarising rolling trailing-twelve-month (TTM) metrics —
    one row per 4-quarter window, stepping back a quarter at a time (up to 5
    windows). Each row sums revenue / gross profit / operating income / net income
    over the window and pairs it with FCF (from the matching cash-flow rows), so
    you can read the annualised revenue trajectory and margin trends across rolling
    windows. Returns None if fewer than 4 quarters are available."""
    income   = fmp.get("income") or []        # newest first
    cashflow = fmp.get("cashflow") or []
    if len(income) < 4:
        return None
    cf_by_date = {c["date"]: c for c in cashflow}

    today = datetime.now().strftime("%Y-%m-%d")
    lines = []
    for i in range(0, min(5, len(income) - 3)):
        window = income[i:i + 4]
        if len(window) < 4:
            continue
        label = window[0].get("period", "") + " " + str(window[0].get("calendarYear", ""))
        ttm_rev = sum(fnum(q.get("revenue")) for q in window
                      if fnum(q.get("revenue")) is not None)
        ttm_gp  = sum(fnum(q.get("grossProfit")) for q in window
                      if fnum(q.get("grossProfit")) is not None)
        ttm_op  = sum(fnum(q.get("operatingIncome")) for q in window
                      if fnum(q.get("operatingIncome")) is not None)
        ttm_ni  = sum(fnum(q.get("netIncome")) for q in window
                      if fnum(q.get("netIncome")) is not None)
        ttm_fcf = sum(fnum(cf_by_date.get(q.get("date"), {}).get("freeCashFlow"))
                      for q in window
                      if fnum(cf_by_date.get(q.get("date"), {}).get("freeCashFlow")) is not None)
        gm         = ttm_gp / ttm_rev if ttm_rev else None
        fcf_margin = ttm_fcf / ttm_rev if ttm_rev else None
        lines.append(
            f"TTM ending {label}: Revenue {money(ttm_rev)} | "
            f"Gross Margin {pct(gm)} | Op Income {money(ttm_op)} | "
            f"Net Income {money(ttm_ni)} | FCF {money(ttm_fcf)} "
            f"(FCF Margin {pct(fcf_margin)})"
        )

    text = (
        f"{name} ({ticker}) — Rolling TTM Summary\n"
        f"Generated: {today} | Source: computed from FMP data\n\n"
        + "\n".join(reversed(lines))
        + "\n\nNote: Each row = trailing 4 quarters ending at that period. "
          "Use to assess annualised revenue trajectory and margin trends across "
          "rolling windows."
    )

    return {"text": text, "source": f"{ticker} Rolling TTM Summary",
            "source_type": "computed_metrics", "period": income[0].get("date", "") if income else "",
            "form_type": "rolling_ttm"}


# ==============================================================================
# STEP 3b — PRE-COMPUTED TREND ANALYSIS
# ==============================================================================

def compute_trend_analysis(name, ticker, fmp):
    """Build one trend-analysis chunk from the quarterly FMP data: per-quarter YoY
    revenue growth, gross margin, FCF margin and SBC % of revenue, plus trend
    direction (last 2 quarters vs prior 2) and inflection points. Any metric with
    fewer than 3 quarters is labelled INSUFFICIENT DATA. Best-effort: never raises
    — returns None if nothing useful can be computed."""
    try:
        income   = fmp.get("income") or []
        cashflow = fmp.get("cashflow") or []
        if not income:
            return None
        cf_by_date = {r.get("date"): r for r in cashflow}

        def fy_of(r):
            return str(r.get("fiscalYear") or r.get("calendarYear") or "")

        # revenue keyed by (period, fiscal_year) for same-quarter-prior-year YoY
        rev_by_key = {}
        for r in income:
            rev_by_key[(str(r.get("period") or "").upper(), fy_of(r))] = fnum(r.get("revenue"))

        # parse each quarter, oldest -> newest
        quarters = []
        for row in sorted(income, key=lambda r: r.get("date", "")):
            per = str(row.get("period") or "").upper()
            fy  = fy_of(row)
            rev = fnum(row.get("revenue"))
            gp  = fnum(row.get("grossProfit"))
            cf  = cf_by_date.get(row.get("date"), {})
            sbc = fnum(cf.get("stockBasedCompensation"))
            fcf = fnum(cf.get("freeCashFlow"))
            yoy = None
            prior = rev_by_key.get((per, str(int(fy) - 1))) if fy.isdigit() else None
            if rev and prior:
                yoy = (rev - prior) / prior * 100
            quarters.append({
                "label":      f"{per} FY{fy}",
                "rev_yoy":    yoy,
                "gm":         (gp / rev * 100) if (gp is not None and rev) else None,
                "fcf_margin": (fcf / rev * 100) if (fcf is not None and rev) else None,
                "sbc_pct":    (sbc / rev * 100) if (sbc is not None and rev) else None,
            })

        def metric_block(title, noun, key, fmt):
            pts = [(qq["label"], qq[key]) for qq in quarters if qq[key] is not None]
            if len(pts) < 3:
                return (f"{title}:\n"
                        f"  INSUFFICIENT DATA ({len(pts)} quarter(s) available — need at least 3).\n")
            vals = [v for _, v in pts]
            series = " → ".join(f"{lab}: {fmt(v)}" for lab, v in pts)

            recent = sum(vals[-2:]) / 2
            prior_grp = vals[-4:-2] if len(vals) >= 4 else vals[:-2]
            delta = recent - sum(prior_grp) / len(prior_grp)
            if abs(delta) <= 1.0:
                direction = f"STABLE (within 1%; range: {fmt(min(vals))} to {fmt(max(vals))})"
            elif delta > 0:
                direction = f"ACCELERATING ({delta:+.1f}pp, last 2 quarters vs prior 2)"
            else:
                direction = f"DECELERATING ({delta:+.1f}pp, last 2 quarters vs prior 2)"

            deltas = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
            dirs = [1 if d > 0 else (-1 if d < 0 else 0) for d in deltas]
            flip = None
            for i in range(1, len(dirs)):
                if dirs[i] != 0 and dirs[i - 1] != 0 and dirs[i] != dirs[i - 1]:
                    flip = i
            if flip is not None:
                run = 0
                j = flip - 1
                while j >= 0 and dirs[j] == dirs[flip - 1]:
                    run += 1
                    j -= 1
                turned = "turned upward" if dirs[flip] > 0 else "turned downward"
                prior_word = "decline" if dirs[flip - 1] < 0 else "increase"
                infl = (f"{noun} {turned} in {pts[flip + 1][0]} after "
                        f"{run} consecutive quarter(s) of {prior_word}.")
            else:
                infl = "None detected."

            return (f"{title}:\n"
                    f"  {series}\n"
                    f"  Direction: {direction}\n"
                    f"  Inflection: {infl}\n")

        # analyst consensus (FMP gives a current snapshot only — no 3-month history)
        consensus = "ANALYST CONSENSUS TREND:\n  Not available.\n"
        try:
            analyst = fmp.get("analyst") or []
            if analyst:
                a = analyst[0] or {}
                consensus = (
                    "ANALYST CONSENSUS TREND:\n"
                    f"  Now: {int(fnum(a.get('strongBuy')) or 0)} strong buy / "
                    f"{int(fnum(a.get('buy')) or 0)} buy / "
                    f"{int(fnum(a.get('hold')) or 0)} hold / "
                    f"{int(fnum(a.get('sell')) or 0)} sell / "
                    f"{int(fnum(a.get('strongSell')) or 0)} strong sell\n"
                )
        except Exception:
            pass

        today = datetime.now().strftime("%Y-%m-%d")
        text = (
            f"{name} ({ticker}) — Pre-Computed Trend Analysis\n"
            f"Generated: {today} | Source: computed from ingested financial data\n\n"
            + metric_block("REVENUE GROWTH TREND (YoY)", "Revenue growth", "rev_yoy",
                           lambda v: f"{v:+.0f}%") + "\n"
            + metric_block("GROSS MARGIN TREND", "Gross margin", "gm",
                           lambda v: f"{v:.1f}%") + "\n"
            + metric_block("FCF MARGIN TREND", "FCF margin", "fcf_margin",
                           lambda v: f"{v:.0f}%") + "\n"
            + metric_block("SBC AS % OF REVENUE", "SBC % of revenue", "sbc_pct",
                           lambda v: f"{v:.0f}%") + "\n"
            + consensus
        ).rstrip()

        most_recent = income[0].get("date", "")
        return {"text": text, "source": f"{name} Trend Analysis",
                "source_type": "trend_analysis", "period": most_recent,
                "form_type": "trend_analysis"}
    except Exception as e:
        print(f"   ⚠️  Warning: trend analysis failed ({e}) — continuing without it.")
        return None


# ==============================================================================
# STEP 4b — FMP EARNINGS-CALL TRANSCRIPTS
# ==============================================================================

def pull_fmp_transcripts(ticker: str) -> list[dict]:
    """Pull the last 12 quarters of FMP earnings-call transcripts and split each
    into two section chunks — prepared_remarks and qa — at most one of each per
    quarter (an empty section is skipped). Best-effort: logs warnings and returns
    [] on a missing key or any API/parse error — never raises, so a failure here
    can never break the ingest.

    Returns a list of dicts: {text, ticker, source, quarter, year, date,
    chunk_type}."""
    chunks: list[dict] = []

    if not FMP_API_KEY:
        print("   ⚠️  Warning: FMP_API_KEY not set — skipping earnings-call transcripts.")
        return chunks

    print("\n  📞 Pulling FMP earnings-call transcripts (last 12 quarters)...")

    # Last 12 quarters counting back from today (dynamic — no hardcoded years).
    now   = datetime.now()
    y, q  = now.year, (now.month - 1) // 3 + 1
    periods = []
    for _ in range(12):
        periods.append((y, q))
        q -= 1
        if q == 0:
            q, y = 4, y - 1

    qa_markers = ("question-and-answer", "operator instructions")

    for year, quarter in periods:
        url = (
            "https://financialmodelingprep.com/stable/earning-call-transcript"
            f"?symbol={ticker}&year={year}&quarter={quarter}&apikey={FMP_API_KEY}"
        )
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            payload = resp.json()
        except Exception as e:
            print(f"   ⚠️  Q{quarter} {year}: fetch failed ({e}) — skipping.")
            continue

        if not payload or not isinstance(payload, list):
            continue
        record  = payload[0] or {}
        content = (record.get("content") or "").strip()
        if not content:
            continue
        date = record.get("date", "")

        # Q&A boundary: first position of any marker (case-insensitive). Turns at
        # or after it are Q&A; everything before is prepared remarks.
        low      = content.lower()
        marks    = [low.find(m) for m in qa_markers if low.find(m) != -1]
        qa_start = min(marks) if marks else -1

        # One chunk per section per quarter: everything before the Q&A boundary is
        # prepared remarks; everything from the boundary onward is Q&A. An empty
        # section is skipped (no empty chunk emitted).
        prepared_text = content[:qa_start].strip() if qa_start != -1 else content.strip()
        qa_text       = content[qa_start:].strip() if qa_start != -1 else ""

        sections = 0
        if prepared_text:
            chunks.append({
                "text":       prepared_text,
                "ticker":     ticker,
                "source":     f"{ticker} Earnings Call Q{quarter} {year} — Prepared Remarks",
                "quarter":    quarter,
                "year":       year,
                "date":       date,
                "chunk_type": "prepared_remarks",
            })
            sections += 1
        if qa_text:
            chunks.append({
                "text":       qa_text,
                "ticker":     ticker,
                "source":     f"{ticker} Earnings Call Q{quarter} {year} — Q&A",
                "quarter":    quarter,
                "year":       year,
                "date":       date,
                "chunk_type": "qa",
            })
            sections += 1
        if sections:
            print(f"   ✅ Q{quarter} {year}: {sections} sections ({date})")

    print(f"   FMP transcripts: {len(chunks)} section chunks total.")
    return chunks


# ==============================================================================
# STEP 5 — MONGODB ATLAS INGESTION
# ==============================================================================

def embed_texts(gemini_client, texts):
    """Embed a list of texts with Gemini gemini-embedding-001 -> list of vectors."""
    result = gemini_client.models.embed_content(
        model=GEMINI_EMBED_MODEL,
        contents=texts,
    )
    return [list(e.values) for e in result.embeddings]


def get_latest_ingest_version(company_key):
    """Return (ingested_at, ingest_version) for the most recent ingest of a
    company — the row carrying the highest ingest_version. Returns (None, 0)
    when the company has never been ingested, or its data predates versioning
    (no ingest_version field). Reads MongoDB Atlas only; safe to import from
    main.py for retrieval-time version pinning."""
    col = get_db()[COMPANY_COLLECTION]
    doc = col.find_one(
        {"company": company_key, "ingest_version": {"$exists": True}},
        sort=[("ingest_version", -1)],
        projection={"ingest_version": 1, "ingested_at": 1, "_id": 0},
    )
    if not doc:
        return None, 0
    return doc.get("ingested_at"), doc.get("ingest_version", 0)


def ingest(chunks, company_key, ticker, exchange, append):
    """Embed each chunk with Gemini and load into the Atlas `company_financials`
    collection (same schema/vector space main.py queries). Returns (added, total)."""
    if not chunks:
        print("\n  ERROR: no chunks were built — nothing to ingest.")
        sys.exit(1)

    # Imported here (not at module top) so --audit/--list never touch the Gemini
    # client — they only read MongoDB.
    try:
        from google import genai
    except ImportError:
        sys.exit("ERROR: 'google-genai' is not installed.  Run:  py -3.11 -m pip install google-genai")

    if not GOOGLE_API_KEY:
        sys.exit("ERROR: GOOGLE_API_KEY is not set (.env). Cannot embed.")
    if not MONGODB_URI:
        sys.exit("ERROR: MONGODB_URI is not set (.env). Cannot connect to MongoDB.")

    iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # Auto-incremented version: max existing ingest_version for this company + 1
    # (1 on first ingest). History is preserved — main.py retrieves only the
    # latest version.
    _, prev_version = get_latest_ingest_version(company_key)
    ingest_version  = prev_version + 1

    # Build documents — metadata stored inline, mirroring main.py's company schema.
    # The _id is version-scoped so a re-ingest is stored ALONGSIDE prior versions
    # (rather than overwriting them via the replace_one upsert below).
    docs = []
    for n, c in enumerate(chunks):
        safe = re.sub(r"[^a-zA-Z0-9]", "_", f"{ticker}_{c['source_type']}_{c.get('period','')}")[:60]
        docs.append({
            "_id":            f"{safe}_{n}_v{ingest_version}",
            "text":           c["text"],
            "company":        company_key,
            "ticker":         ticker,
            "source":         c["source"],
            "source_type":    c["source_type"],
            "period":         c.get("period", "Unknown"),
            "form_type":      c.get("form_type", "Unknown"),
            "ingested_at":    iso,
            "ingest_version": ingest_version,
        })

    # --- FMP earnings-call transcripts (best-effort; pull_fmp_transcripts never
    # raises). Appended to `docs` shaped like the SEC/yfinance docs so the
    # embedding + write loops below need no changes. ---
    fmp_chunks = pull_fmp_transcripts(ticker)
    base = len(docs)
    for j, c in enumerate(fmp_chunks):
        n    = base + j
        safe = re.sub(r"[^a-zA-Z0-9]", "_", f"{ticker}_{c['chunk_type']}_Q{c['quarter']}_{c['year']}")[:60]
        docs.append({
            "_id":            f"{safe}_{n}_v{ingest_version}",
            "text":           c["text"],
            "company":        company_key,
            "ticker":         ticker,
            "source":         c["source"],
            "source_type":    "earnings_transcript",
            "period":         c.get("date") or str(c.get("year", "Unknown")),
            "form_type":      f"Earnings Call ({c['chunk_type']})",
            "quarter":        c.get("quarter"),
            "year":           c.get("year"),
            "ingested_at":    iso,
            "ingest_version": ingest_version,
        })

    # Embed with Gemini — imported here so --audit / --list never load it.
    from google import genai as google_genai

    print(f"\n  Embedding model: {GEMINI_EMBED_MODEL}")
    gemini_client = google_genai.Client(api_key=GOOGLE_API_KEY)
    print(f"  Embedding {len(docs):,} chunks via Gemini...")
    for i, d in enumerate(docs, 1):
        result = gemini_client.models.embed_content(
            model=GEMINI_EMBED_MODEL,
            contents=d["text"],
        )
        d["embedding"] = list(result.embeddings[0].values)
        if i % 10 == 0 or i == len(docs):
            print(f"    embedded {i:,} / {len(docs):,}")

    print("  Connecting to MongoDB Atlas...")
    col = get_db()[COMPANY_COLLECTION]

    # Versioned ingest — never delete prior data. Older versions stay in the
    # collection as history; the debate engine only retrieves the latest version.
    # (--append is retained for CLI compatibility but no longer changes behaviour,
    # since ingestion is now non-destructive by default.)
    prior = col.count_documents({"company": company_key})
    if prior:
        print(f"  Preserving {prior:,} existing '{company_key}' chunk(s); writing as version {ingest_version}.")
    else:
        print(f"  First ingest for '{company_key}' — writing as version {ingest_version}.")

    # Upsert by _id (version-scoped) so re-running the SAME version overwrites
    # rather than duplicates, while a NEW version is added alongside the old.
    for n, d in enumerate(docs, 1):
        col.replace_one({"_id": d["_id"]}, d, upsert=True)
        if n % 25 == 0 or n == len(docs):
            print(f"  Loaded {n:,} / {len(docs):,} chunks")

    return len(docs), col.count_documents({})


# ==============================================================================
# AUDIT — --audit <company>   (reads MongoDB Atlas)
# ==============================================================================

def list_companies():
    """Print every distinct company key stored in company_financials, with counts.
    Lets you confirm the exact name to pass to --company / --audit. Reads Atlas."""
    bar = "═" * 56
    col = get_db()[COMPANY_COLLECTION]

    print(f"\n{bar}")
    print(f"COMPANIES IN '{COMPANY_COLLECTION}'")
    print(f"COMPANIES IN '{COMPANY_COLLECTION}'")
    print(bar)

    if col.count_documents({}) == 0:
        print("(collection is empty — nothing ingested)")
        print(bar)
        return

    rows = col.aggregate([
        {"$group": {"_id": "$company",
                    "n": {"$sum": 1},
                    "tickers": {"$addToSet": "$ticker"}}},
        {"$sort": {"n": -1, "_id": 1}},
    ])
    for row in rows:
        name = row["_id"]
        ticker = next((t for t in row.get("tickers", []) if t), "")
        tag = f"  ({ticker})" if ticker else ""
        print(f"   {row['n']:>4} chunks   {name}{tag}")
    print(bar)
    print("Pass the exact name above to --company (debates) or --audit (detail).")


def run_audit(company_arg):
    company_key = normalize_company(company_arg)
    bar = "═" * 56

    col = get_db()[COMPANY_COLLECTION]
    docs = list(col.find({"company": company_key}))

    print(f"\n{bar}")
    print(f"COMPANY FINANCIALS AUDIT — {company_arg}")
    print(bar)

    if not docs:
        avail = sorted(c for c in col.distinct("company") if c)
        print(f"No chunks found for '{company_key}'.")
        if avail:
            print(f"Companies currently in the collection: {', '.join(avail)}")
        print(bar)
        return

    # In MongoDB the document and its metadata are a single record.
    metas = docs

    print(f"Total chunks : {len(metas)}\n")

    def tally(field):
        counts = {}
        for m in metas:
            counts[m.get(field, "Unknown")] = counts.get(m.get(field, "Unknown"), 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    print("BY SOURCE TYPE:")
    for k, v in tally("source_type"):
        print(f"   {v:>3}  {k}")

    print("\nBY FORM TYPE:")
    for k, v in tally("form_type"):
        print(f"   {v:>3}  {k}")

    print("\nBY PERIOD (most recent first):")
    rows = sorted(
        [(m.get("period", "Unknown"), m.get("form_type", "Unknown")) for m in metas],
        key=lambda r: r[0], reverse=True,
    )
    for period, form in rows[:15]:
        print(f"   {period}  {form}")
    if len(rows) > 15:
        print(f"   ... (+{len(rows) - 15} more)")

    # sample: most recent ANNUAL summary, then most recent QUARTER (for comparison)
    inc = [(m, m.get("text", "")) for m in metas if m.get("form_type") == "income_statement"]
    annual_inc  = sorted([x for x in inc if "Annual" in (x[0].get("source") or "")],
                         key=lambda md: md[0].get("period", ""), reverse=True)
    quarter_inc = sorted([x for x in inc if "Annual" not in (x[0].get("source") or "")],
                         key=lambda md: md[0].get("period", ""), reverse=True)
    if annual_inc or quarter_inc:
        print("\nSAMPLE CHUNK (most recent annual summary, then most recent quarter):")
        print("─" * 48)
        if annual_inc:
            print(annual_inc[0][1][:600])
        if annual_inc and quarter_inc:
            print("─" * 48)
        if quarter_inc:
            print(quarter_inc[0][1][:600])
        print("─" * 48)

    # coverage check
    def count_form(f):
        return sum(1 for m in metas if m.get("form_type") == f)

    def has_type(t):
        return any(m.get("source_type") == t for m in metas)

    def has_form_contains(sub):
        return any(sub in (m.get("form_type") or "") for m in metas)

    def mark(ok):
        return "✅" if ok else "⚠️ "

    inc_n, bal_n, cf_n = count_form("income_statement"), count_form("balance_sheet"), count_form("cash_flow")

    print("\nCOVERAGE CHECK:")
    print(f"   {mark(inc_n>0)} Income statements    : {inc_n} quarters")
    print(f"   {mark(bal_n>0)} Balance sheets       : {bal_n} quarters")
    print(f"   {mark(cf_n>0)} Cash flow            : {cf_n} quarters")
    print(f"   {mark(has_type('computed_metrics'))} Computed metrics     : {'present' if has_type('computed_metrics') else 'missing'}")
    print(bar)


# ==============================================================================
# NON-INTERACTIVE AUTO-INGEST (for servers / background callers)
# ==============================================================================

def ingest_by_ticker(ticker, display=None, exchange=None, progress=None):
    """Non-interactive auto-ingest of a company's financials by ticker.

    The same pipeline as main() — FMP pull -> build chunks -> embed + store in the
    MongoDB Atlas `company_financials` collection (versioned, non-destructive) —
    but with NO interactive confirmation (no resolve_ticker prompt), so it is safe
    to call from a server or a background thread (e.g. the FastAPI /debate/start
    auto-ingest path).

    `display` overrides the company display name (defaults to the FMP profile name,
    then the ticker). `progress`, if given, is called with a short status string at
    the start of each phase. Returns (company_key, added, total). Raises ValueError
    when FMP has no usable financial data for the ticker (rather than sys.exit, so
    callers can surface a clean error instead of killing their process)."""
    def _say(msg):
        if progress:
            progress(msg)

    ticker = ticker.upper()

    # STEP 1/2 — FMP data pull (skips the interactive resolve_ticker confirmation).
    _say("Pulling financial statements from FMP…")
    try:
        fmp = pull_fmp_financials(ticker)
    except SystemExit as exc:
        # pull_fmp_financials uses sys.exit() as its error channel; convert it to a
        # normal exception so server callers don't get a SystemExit.
        raise ValueError(str(exc) or f"FMP has no data for '{ticker}'.")
    if not fmp.get("income"):
        raise ValueError(f"FMP returned no income-statement data for '{ticker}'.")

    profile = fmp.get("profile") or []
    display = display or (profile[0].get("companyName") if profile else None) or ticker

    # company KEY used for storage + retrieval — identical derivation to main() so
    # the stored key matches what the debate engine queries by.
    clean = re.sub(r",?\s+(inc\.?|incorporated|corp\.?|corporation|co\.?|ltd\.?|plc|holdings|group)\b",
                   "", display, flags=re.IGNORECASE)
    clean = clean.strip().rstrip(".,").strip()
    company_key = normalize_company(clean)

    # STEP 3 — build all chunks (pure transforms over the FMP data; no network).
    _say("Building financial summaries…")
    chunks = build_market_chunks(display, ticker, fmp)
    computed = build_computed_chunk(display, ticker, fmp)
    if computed:
        chunks.append(computed)
    ttm = compute_ttm_chunk(display, ticker, fmp)
    if ttm is not None:
        chunks.append(ttm)
    trend = compute_trend_analysis(display, ticker, fmp)
    if trend:
        chunks.append(trend)

    # STEP 5 — embed + store (one Gemini embedding per chunk; versioned ingest).
    _say("Embedding and storing in the knowledge base…")
    added, total = ingest(chunks, company_key, ticker, exchange, False)
    return company_key, added, total


# ==============================================================================
# MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Auto-ingest a company's financials (FMP) into MongoDB Atlas.")
    parser.add_argument("--ticker",   type=str, help="Ticker symbol, e.g. MDB")
    parser.add_argument("--exchange", type=str, default=None,
                        help="Exchange, e.g. NASDAQ (display/metadata only — FMP fetches by ticker)")
    parser.add_argument("--append",   action="store_true", help="Add alongside existing company data instead of wiping")
    parser.add_argument("--audit",    type=str, default=None, metavar="COMPANY",
                        help="Audit what is already ingested for COMPANY, then exit")
    parser.add_argument("--list",      action="store_true",
                        help="List every company stored in company_financials (and exit)")
    args = parser.parse_args()

    # ── List mode: just show what's stored, then exit (reads MongoDB only) ──
    if args.list:
        list_companies()
        return

    # ── Audit mode: no ingestion, no FMP pull needed ──
    if args.audit:
        run_audit(args.audit)
        return

    if not args.ticker:
        print("\n  ERROR: --ticker is required (or use --audit <company>).")
        sys.exit(1)

    start = datetime.now()
    ticker = args.ticker.upper()

    # STEP 1 — validate ticker via FMP /profile (and confirm with the user)
    print(f"\n  Validating ticker '{ticker}' via FMP...")
    resolved = resolve_ticker(ticker, args.exchange)
    exchange = resolved["exchange"]

    # STEP 2 — FMP data pull (profile / income / balance / cash flow / metrics / analyst)
    fmp = pull_fmp_financials(ticker)

    display = resolved["name"] or ticker

    # company KEY used for storage + retrieval (matches main.py's normalize_company)
    clean = re.sub(r",?\s+(inc\.?|incorporated|corp\.?|corporation|co\.?|ltd\.?|plc|holdings|group)\b",
                   "", display, flags=re.IGNORECASE)
    clean = clean.strip().rstrip(".,").strip()   # drop any trailing period/comma ("MongoDB." -> "MongoDB")
    company_key = normalize_company(clean)

    if not fmp.get("income"):
        print("\n  ERROR: FMP returned no income statement data for this ticker.")
        print("  Check the ticker, or verify your FMP plan covers this symbol.")
        sys.exit(1)

    market_chunks = build_market_chunks(display, ticker, fmp)

    # STEP 3 — computed
    computed = build_computed_chunk(display, ticker, fmp)
    computed_chunks = [computed] if computed else []

    # STEP 3a — rolling TTM summary (one row per trailing-4-quarter window)
    ttm = compute_ttm_chunk(display, ticker, fmp)
    if ttm is not None:
        all_chunks_ttm = [ttm]
    else:
        all_chunks_ttm = []

    # STEP 3b — pre-computed trend analysis (directions + inflection points)
    trend = compute_trend_analysis(display, ticker, fmp)
    trend_chunks = [trend] if trend else []

    all_chunks = market_chunks + computed_chunks + all_chunks_ttm + trend_chunks

    # STEP 5 — ingest
    added, total = ingest(all_chunks, company_key, ticker, exchange, args.append)

    # STEP 6 — summary
    def count(stype):
        return sum(1 for c in all_chunks if c["source_type"] == stype)

    elapsed = (datetime.now() - start).total_seconds()
    bar = "═" * 56
    print(f"\n{bar}")
    print(f"✅ INGEST COMPLETE — {display} ({ticker}) {exchange}")
    print(bar)
    trend_count = sum(1 for c in all_chunks if c.get("source_type") == "trend_analysis")
    print(f"   FMP financials      : {count('yfinance_financials'):>3} chunks")
    print(f"   FMP metrics         : {count('yfinance_metrics'):>3} chunks")
    print(f"   Computed metrics    : {count('computed_metrics'):>3} chunks")
    print(f"   Trend analysis      : {trend_count:>3} chunk{'s' if trend_count != 1 else ''}")
    print("   ──────────────────────────────")
    print(f"   Total added         : {added:>3} chunks")
    print(f"   Collection          : {COMPANY_COLLECTION} ({total} total docs)")
    print(f"   Collection          : {COMPANY_COLLECTION} ({total} total docs)")
    print(f"   Stored as           : company='{company_key}'  (use this with --company)")
    print(f"   Time taken          : {elapsed:.1f}s")
    print(bar)
    print(f"\nRun audit to verify:")
    print(f"   python scripts/analyse_company.py --audit {company_key}")
    print(f"\nReady to debate:")
    print(f'   py -3.11 main.py --topic "Is {display} a good investment?" --company {company_key} --agents buffett howard_marks')


if __name__ == "__main__":
    main()
