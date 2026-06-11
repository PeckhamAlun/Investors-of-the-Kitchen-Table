"""
==============================================================================
  ANALYSE COMPANY — AUTO INGESTION via yfinance + SEC EDGAR
==============================================================================

  Pulls a company's financials automatically (no manual PDF dropping) and
  loads them into the shared `company_financials` ChromaDB collection so the
  debate engine can cite real numbers.

  Data layers, all written as clean human-readable text chunks:
    1. yfinance financials — income / balance / cash-flow, one quarter per chunk
    2. yfinance metrics    — valuation/margin snapshot + analyst recommendations
    3. Computed metrics    — SBC %, FCF margin, YoY growth, gross-margin, Rule of 40
    4. SEC EDGAR           — last 4x 10-Q + 1x 10-K: MD&A, Risk Factors, Business

  This is the planned-feature sibling of ingest_company.py (PDF-based). It does
  NOT touch ingest_company.py and writes to the SAME collection with the SAME
  wipe/append semantics, so the two are interchangeable per company.

  USAGE
  -----
    py -3.11 scripts/analyse_company.py --ticker MDB
    py -3.11 scripts/analyse_company.py --ticker MDB --exchange NASDAQ
    py -3.11 scripts/analyse_company.py --ticker MDB --append
    py -3.11 scripts/analyse_company.py --audit MongoDB

  DEPENDENCIES
  ------------
    py -3.11 -m pip install yfinance
    (sentence-transformers, chromadb already present from the rest of the project)

  No API key required — yfinance is free and keyless. SEC EDGAR is keyless too.

  NOTE ON COMPANY NAMING
  ----------------------
  The stored `company` metadata is routed through config.normalize_company() —
  the SAME function main.py uses on --company — so retrieval always matches.
  Known camel-case names (MongoDB, CrowdStrike, ...) are preserved via the
  override table in config.py.
==============================================================================
"""

import os
import re
import sys
import time
import html
import argparse
from datetime import datetime

# Force UTF-8 stdout/stderr so the ═ box-drawing chars and emoji in the banners
# don't raise UnicodeEncodeError on a Windows cp1252 console (CLAUDE.md §11).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── torch + yfinance: ingest-only, and ORDER MATTERS ────────────────────────
# Neither is needed by --audit or --list (those only read ChromaDB), so we skip
# both in those modes — keeping them light and free of native-library conflicts.
# When we DO import them, torch MUST come before yfinance: yfinance's native deps
# initialise a runtime that makes torch's c10.dll fail to initialise (OSError
# [WinError 1114]) if torch is imported afterwards. Confirmed empirically:
# `import yfinance; import torch` -> WinError 1114, but `import torch; import yfinance` -> OK.
if not any(flag in sys.argv for flag in ("--audit", "--list")):
    try:
        import torch  # noqa: F401  (side-effect import: fixes DLL load order)
    except Exception:
        pass  # a genuine torch problem will resurface with a clear trace in ingest()
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("ERROR: 'yfinance' is not installed.  Run:  py -3.11 -m pip install yfinance")

# ── third-party (always needed: SEC uses requests; audit/list use chromadb) ──
try:
    import requests
except ImportError:
    sys.exit("ERROR: 'requests' is not installed.  Run:  py -3.11 -m pip install requests")

try:
    import chromadb
except ImportError:
    sys.exit("ERROR: 'chromadb' is not installed.")

# NOTE: sentence_transformers is imported lazily inside ingest() so --audit never
# loads it. In ingest mode torch is already initialised by the load-order import
# above (before yfinance), so SentenceTransformer just reuses it.

# ── project config (single source of truth) ──
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    CHROMA_DIR,
    EMBED_MODEL,
    CHUNK_SIZE,
    CHUNK_OVERLAP,
    COMPANY_COLLECTION,
    normalize_company,
)

# ==============================================================================
# CONSTANTS
# ==============================================================================

SEC_UA     = {"User-Agent": "KitchenTable research@kitchentable.com"}
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

# SEC section character caps (one chunk per section, per spec)
CAP_MDNA   = 8000
CAP_RISK   = 3000
CAP_BIZ    = 3000


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
# yfinance DATAFRAME HELPERS
# ==============================================================================

def make_ticker(symbol):
    """
    Build a yf.Ticker backed by a requests Session with a browser-like
    User-Agent. Yahoo Finance rate-limiting can key off the session/User-Agent
    (not just the IP), so a realistic UA reduces 429s. Use this for EVERY
    yf.Ticker() call in the script.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })
    return yf.Ticker(symbol, session=session)


def ncols(df):
    """Number of period columns in a yfinance statement DataFrame (0 if empty)."""
    try:
        return 0 if df is None or df.empty else len(df.columns)
    except Exception:
        return 0


def col_date(col):
    """Statement column (a Timestamp) -> 'YYYY-MM-DD' string."""
    try:
        return col.strftime("%Y-%m-%d")
    except Exception:
        try:
            return str(col.date())
        except Exception:
            return str(col)


def sorted_cols(df):
    """Period columns, most-recent first."""
    if df is None or getattr(df, "empty", True):
        return []
    try:
        return sorted(list(df.columns), reverse=True)
    except Exception:
        return list(df.columns)


def cell(df, labels, col):
    """
    Value at the first matching row label for a given period column, or None.
    `labels` is a list of candidate row names (yfinance label wording varies).
    """
    if df is None or getattr(df, "empty", True):
        return None
    for lbl in labels:
        if lbl in df.index:
            try:
                return fnum(df.at[lbl, col])
            except Exception:
                continue
    return None


def prior_year_col(cols, col):
    """Find the column ~1 year before `col` (±25 days), for YoY. None if absent."""
    try:
        for c in cols:
            delta = (col - c).days
            if 340 <= delta <= 390:
                return c
    except Exception:
        return None
    return None


# ==============================================================================
# STEP 1 — TICKER VALIDATION / RESOLUTION (yfinance, keyless)
# ==============================================================================

def ticker_is_valid(info):
    if not info:
        return False
    if info.get("symbol") or info.get("longName") or info.get("shortName"):
        return True
    return info.get("regularMarketPrice") is not None


def resolve_ticker(ticker, info, exchange):
    """
    Validate the ticker via yfinance .info and confirm with the user.
    --exchange (if given) is used only for display/metadata, not data fetching.
    Returns dict: {name, ticker, exchange, sector, industry}.
    """
    ticker = ticker.upper()

    name     = info.get("longName") or info.get("shortName") or ticker
    sector   = info.get("sector")
    industry = info.get("industry")
    exch     = (exchange or info.get("exchange") or info.get("fullExchangeName") or "UNKNOWN")
    if exchange:
        exch = exchange.upper()

    print(f"\n  🔍 Found: {name} ({ticker}) — {exch}")
    if sector or industry:
        print(f"     Sector: {sector or '?'} | Industry: {industry or '?'}")
    confirm = input("     Confirm ingestion? [Y/n]: ").strip().lower()
    if confirm in ("n", "no"):
        print("  Cancelled.")
        sys.exit(0)

    return {"name": name, "ticker": ticker, "exchange": exch,
            "sector": sector, "industry": industry}


# ==============================================================================
# STEP 2 — yfinance DATA PULL + chunk building
# ==============================================================================

def pull_yf(ticker_obj, info):
    """Pull all yfinance frames. Returns dict of DataFrames + info + recommendations."""
    data = {"info": info}

    print("\n  ── yfinance data pull ──")

    print("  📊 Pulling income statements...", end="", flush=True)
    data["income_q"] = ticker_obj.quarterly_financials
    data["income_a"] = ticker_obj.financials
    print(f"   ✅ {ncols(data['income_q'])} quarters / {ncols(data['income_a'])} years")

    print("  📊 Pulling balance sheets...   ", end="", flush=True)
    data["balance_q"] = ticker_obj.quarterly_balance_sheet
    data["balance_a"] = ticker_obj.balance_sheet
    print(f"   ✅ {ncols(data['balance_q'])} quarters")

    print("  📊 Pulling cash flow...        ", end="", flush=True)
    data["cashflow_q"] = ticker_obj.quarterly_cashflow
    data["cashflow_a"] = ticker_obj.cashflow
    print(f"   ✅ {ncols(data['cashflow_q'])} quarters")

    print("  📊 Pulling analyst recommendations...", end="", flush=True)
    try:
        data["recommendations"] = ticker_obj.recommendations
    except Exception:
        data["recommendations"] = None
    print(f"   ✅ {ncols(data.get('recommendations'))} rows")

    print("  📊 Reading company profile...  ", end="", flush=True)
    print(f"   ✅ {'1 record' if info else 'none'}")

    return data


def fiscal_year_quarter(period, fye_month):
    """(fiscal_year, quarter) for a period-end 'YYYY-MM-DD', given the fiscal
    year-end month. Ending-year convention: a Jan-31-2026 year-end is FY2026,
    and the quarter ending Apr-30-2025 is Q1 FY2026."""
    y, m = int(period[:4]), int(period[5:7])
    fy = y if m <= fye_month else y + 1
    months_in = ((m - (fye_month + 1)) % 12) + 1   # 1..12; quarter-ends land on 3/6/9/12
    q = (months_in - 1) // 3 + 1
    return fy, q


def detect_fye_month(data):
    """Fiscal year-end month (1-12), inferred from the most recent annual column
    (falls back to the most recent quarter, then December)."""
    for key in ("income_a", "balance_a", "cashflow_a", "income_q", "balance_q", "cashflow_q"):
        cols = sorted_cols(data.get(key))
        if cols:
            return int(col_date(cols[0])[5:7])
    return 12


def build_market_chunks(name, ticker, data):
    """
    Convert yfinance frames into text chunks.
    Returns a list of dicts: {text, source, source_type, period, form_type}.

    Financials are emitted as: one ANNUAL summary chunk each (income / balance /
    cash flow, last full fiscal year) labelled 'FY2026 Annual', plus the 4 most
    recent QUARTERS each, labelled 'Q1 FY2026' etc. (descending).
    """
    chunks = []
    inc,   bal,   cf    = data["income_q"],     data["balance_q"],     data["cashflow_q"]
    inc_a, bal_a, cf_a  = data.get("income_a"), data.get("balance_a"), data.get("cashflow_a")
    info = data.get("info") or {}

    inc_cols,   bal_cols,   cf_cols    = sorted_cols(inc),   sorted_cols(bal),   sorted_cols(cf)
    inc_a_cols, bal_a_cols, cf_a_cols  = sorted_cols(inc_a), sorted_cols(bal_a), sorted_cols(cf_a)

    fye_month = detect_fye_month(data)

    def annual_label(period):
        return f"FY{int(period[:4])} Annual"

    def quarter_label(period):
        fy, q = fiscal_year_quarter(period, fye_month)
        return f"Q{q} FY{fy}"

    # ---- shared text builders (used by both annual and quarterly) ----
    def income_text(idf, icols, col, cdf, ccols, period, lab):
        rev     = cell(idf, ["Total Revenue", "Revenue", "Operating Revenue"], col)
        gp      = cell(idf, ["Gross Profit"], col)
        op_inc  = cell(idf, ["Operating Income", "Total Operating Income As Reported"], col)
        net_inc = cell(idf, ["Net Income", "Net Income Common Stockholders"], col)
        # Skip periods with no usable data (e.g. the latest period not yet reported)
        if rev is None and gp is None and op_inc is None and net_inc is None:
            return None
        yoy = ""
        pcol = prior_year_col(icols, col)
        if rev and pcol is not None:
            prev = cell(idf, ["Total Revenue", "Revenue", "Operating Revenue"], pcol)
            if prev:
                yoy = f" ({pct((rev - prev) / prev, signed=True)} YoY)"
        gm = (gp / rev) if (gp is not None and rev) else None
        sbc = cell(cdf, ["Stock Based Compensation"], col) if col in ccols else None
        if sbc is not None:
            sbc_pct = f" ({pct(sbc / rev)} of revenue)" if rev else ""
            sbc_line = f"Stock-Based Compensation: {money(sbc)}{sbc_pct}"
        else:
            sbc_line = "Stock-Based Compensation: N/A"
        rnd = cell(idf, ["Research And Development", "Research Development"], col)
        sga = cell(idf, ["Selling General And Administration",
                         "Selling General Administrative",
                         "Selling General And Administrative"], col)
        return (
            f"{name} ({ticker}) — Income Statement {lab}\n"
            f"Period: {period} | Source: yfinance\n\n"
            f"Revenue: {money(rev)}{yoy}\n"
            f"Gross Profit: {money(gp)} (Gross Margin: {pct(gm)})\n"
            f"Operating Income: {money(op_inc)}\n"
            f"Net Income: {money(net_inc)}\n"
            f"{sbc_line}\n"
            f"R&D Expense: {money(rnd)}\n"
            f"Selling, General & Admin: {money(sga)}"
        )

    def balance_text(bdf, col, period, lab):
        return (
            f"{name} ({ticker}) — Balance Sheet {lab}\n"
            f"Period: {period} | Source: yfinance\n\n"
            f"Cash & Equivalents: {money(cell(bdf, ['Cash And Cash Equivalents'], col))}\n"
            f"Cash, Equiv. & Short-Term Investments: {money(cell(bdf, ['Cash Cash Equivalents And Short Term Investments'], col))}\n"
            f"Total Current Assets: {money(cell(bdf, ['Current Assets', 'Total Current Assets'], col))}\n"
            f"Total Assets: {money(cell(bdf, ['Total Assets'], col))}\n"
            f"Total Current Liabilities: {money(cell(bdf, ['Current Liabilities', 'Total Current Liabilities'], col))}\n"
            f"Total Debt: {money(cell(bdf, ['Total Debt'], col))}\n"
            f"Total Liabilities: {money(cell(bdf, ['Total Liabilities Net Minority Interest', 'Total Liabilities'], col))}\n"
            f"Total Stockholders' Equity: {money(cell(bdf, ['Stockholders Equity', 'Total Stockholder Equity'], col))}"
        )

    def cashflow_text(cdf, ccols, col, rdf, rcols, period, lab):
        ocf = cell(cdf, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"], col)
        fcf = cell(cdf, ["Free Cash Flow"], col)
        rev_row = rev_for_date(rdf, rcols, col)
        fcf_margin = f" (FCF Margin: {pct(fcf / rev_row)})" if (fcf is not None and rev_row) else ""
        return (
            f"{name} ({ticker}) — Cash Flow {lab}\n"
            f"Period: {period} | Source: yfinance\n\n"
            f"Operating Cash Flow: {money(ocf)}\n"
            f"Capital Expenditure: {money(cell(cdf, ['Capital Expenditure'], col))}\n"
            f"Free Cash Flow: {money(fcf)}{fcf_margin}\n"
            f"Stock-Based Compensation: {money(cell(cdf, ['Stock Based Compensation'], col))}\n"
            f"Change in Cash: {money(cell(cdf, ['Changes In Cash', 'Change In Cash And Cash Equivalents'], col))}"
        )

    def add(text, source, period, form_type):
        chunks.append({"text": text, "source": source,
                       "source_type": "yfinance_financials", "period": period,
                       "form_type": form_type})

    # ---- ANNUAL: last full fiscal year — one summary chunk each ----
    if inc_a_cols:
        col = inc_a_cols[0]; period = col_date(col); lab = annual_label(period)
        t = income_text(inc_a, inc_a_cols, col, cf_a, cf_a_cols, period, lab)
        if t is None:
            print(f"  ⚠️  Skipping {period} income statement — all fields N/A (data not yet available)")
        else:
            add(t, f"{ticker} Income Statement {lab}", period, "income_statement")
    if bal_a_cols:
        col = bal_a_cols[0]; period = col_date(col); lab = annual_label(period)
        add(balance_text(bal_a, col, period, lab), f"{ticker} Balance Sheet {lab}", period, "balance_sheet")
    if cf_a_cols:
        col = cf_a_cols[0]; period = col_date(col); lab = annual_label(period)
        add(cashflow_text(cf_a, cf_a_cols, col, inc_a, inc_a_cols, period, lab),
            f"{ticker} Cash Flow {lab}", period, "cash_flow")

    # ---- QUARTERLY: 4 most recent quarters, individually (descending) ----
    for col in inc_cols[:4]:
        period = col_date(col); lab = quarter_label(period)
        t = income_text(inc, inc_cols, col, cf, cf_cols, period, lab)
        if t is None:
            print(f"  ⚠️  Skipping {period} income statement — all fields N/A (data not yet available)")
            continue
        add(t, f"{ticker} Income Statement {lab}", period, "income_statement")
    for col in bal_cols[:4]:
        period = col_date(col); lab = quarter_label(period)
        add(balance_text(bal, col, period, lab), f"{ticker} Balance Sheet {lab}", period, "balance_sheet")
    for col in cf_cols[:4]:
        period = col_date(col); lab = quarter_label(period)
        add(cashflow_text(cf, cf_cols, col, inc, inc_cols, period, lab),
            f"{ticker} Cash Flow {lab}", period, "cash_flow")

    # ---- Profile (single chunk, from .info) ----
    if info:
        desc = (info.get("longBusinessSummary") or "")[:1500]
        text = (
            f"{name} ({ticker}) — Company Profile\n"
            f"Source: yfinance\n\n"
            f"Exchange: {info.get('exchange','?')}\n"
            f"Sector: {info.get('sector','?')} | Industry: {info.get('industry','?')}\n"
            f"Market Cap: {money(info.get('marketCap'))}\n"
            f"Price: {money(info.get('currentMarketPrice') or info.get('currentPrice'))} | Beta: {info.get('beta','?')}\n"
            f"Employees: {info.get('fullTimeEmployees','?')} | Country: {info.get('country','?')}\n"
            f"Website: {info.get('website','?')}\n\n"
            f"Description: {desc}"
        )
        chunks.append({"text": text, "source": f"{ticker} Company Profile",
                       "source_type": "yfinance_financials", "period": "current",
                       "form_type": "profile"})

    # ---- Key metrics snapshot (from .info) ----
    if info:
        text = (
            f"{name} ({ticker}) — Key Metrics & Ratios (current snapshot)\n"
            f"Source: yfinance\n\n"
            f"Market Cap: {money(info.get('marketCap'))}\n"
            f"Enterprise Value: {money(info.get('enterpriseValue'))}\n"
            f"Trailing P/E: {info.get('trailingPE','N/A')}\n"
            f"Forward P/E: {info.get('forwardPE','N/A')}\n"
            f"Price/Sales (TTM): {info.get('priceToSalesTrailing12Months','N/A')}\n"
            f"EV/Revenue: {info.get('enterpriseToRevenue','N/A')}\n"
            f"EV/EBITDA: {info.get('enterpriseToEbitda','N/A')}\n"
            f"Gross Margins: {pct(info.get('grossMargins'))}\n"
            f"Operating Margins: {pct(info.get('operatingMargins'))}\n"
            f"Profit Margins: {pct(info.get('profitMargins'))}\n"
            f"Return on Equity: {pct(info.get('returnOnEquity'))}\n"
            f"Revenue Growth (YoY): {pct(info.get('revenueGrowth'), signed=True)}\n"
            f"Total Cash: {money(info.get('totalCash'))} | Total Debt: {money(info.get('totalDebt'))}\n"
            f"Debt/Equity: {info.get('debtToEquity','N/A')} | Current Ratio: {info.get('currentRatio','N/A')}\n"
            f"Free Cash Flow (TTM): {money(info.get('freeCashflow'))}"
        )
        chunks.append({"text": text, "source": f"{ticker} Key Metrics",
                       "source_type": "yfinance_metrics", "period": "current",
                       "form_type": "key_metrics"})

    # ---- Analyst recommendations (best-effort; format varies by yfinance ver) ----
    rec = data.get("recommendations")
    if rec is not None and not getattr(rec, "empty", True):
        try:
            rec_table = rec.tail(15).to_string()
        except Exception:
            rec_table = str(rec)
        text = (
            f"{name} ({ticker}) — Analyst Recommendations\n"
            f"Source: yfinance\n\n{rec_table[:3000]}"
        )
        chunks.append({"text": text, "source": f"{ticker} Analyst Recommendations",
                       "source_type": "yfinance_metrics", "period": "current",
                       "form_type": "analyst_recommendations"})

    return chunks


def rev_for_date(inc, inc_cols, col):
    """Revenue for a specific period column (used by cash-flow FCF margin)."""
    if col in inc_cols:
        return cell(inc, ["Total Revenue", "Revenue", "Operating Revenue"], col)
    return None


# ==============================================================================
# STEP 3 — COMPUTED METRICS
# ==============================================================================

def build_computed_chunk(name, ticker, data):
    """Compute headline trends over the trailing 4 quarters -> one chunk."""
    inc = data["income_q"]
    cf  = data["cashflow_q"]
    inc_cols = sorted_cols(inc)
    cf_cols  = sorted_cols(cf)
    if not inc_cols:
        return None

    today = datetime.now().strftime("%Y-%m-%d")
    sbc_lines, fcf_lines, growth_lines, gm_lines, rule40_lines = [], [], [], [], []

    for col in inc_cols[:4]:
        date = col_date(col)
        rev  = cell(inc, ["Total Revenue", "Revenue", "Operating Revenue"], col)
        sbc  = cell(cf, ["Stock Based Compensation"], col) if col in cf_cols else None
        fcf  = cell(cf, ["Free Cash Flow"], col) if col in cf_cols else None

        if rev and sbc is not None:
            sbc_lines.append(f"  {date}: {pct(sbc / rev)}")

        fcf_margin = None
        if rev and fcf is not None:
            fcf_margin = fcf / rev
            fcf_lines.append(f"  {date}: {pct(fcf_margin)}")

        growth = None
        pcol = prior_year_col(inc_cols, col)
        if rev and pcol is not None:
            prev = cell(inc, ["Total Revenue", "Revenue", "Operating Revenue"], pcol)
            if prev:
                growth = (rev - prev) / prev
                growth_lines.append(f"  {date}: {pct(growth, signed=True)}")

        gp = cell(inc, ["Gross Profit"], col)
        gm = (gp / rev) if (gp is not None and rev) else None
        if gm is not None:
            gm_lines.append(f"  {date}: {pct(gm)}")

        if growth is not None and fcf_margin is not None:
            rule40_lines.append(f"  {date}: {growth*100 + fcf_margin*100:.1f}")

    def block(title, lines):
        if not lines:
            return f"{title}:\n  (insufficient data)\n"
        return f"{title}:\n" + "\n".join(lines) + "\n"

    text = (
        f"{name} ({ticker}) — Computed Metrics Summary\n"
        f"Generated: {today} | Source: computed from yfinance data\n\n"
        + block("SBC as % of Revenue (trailing 4 quarters)", sbc_lines) + "\n"
        + block("FCF Margin (reported)", fcf_lines) + "\n"
        + block("Revenue Growth YoY (quarterly)", growth_lines) + "\n"
        + block("Gross Margin Trend", gm_lines) + "\n"
        + block("Rule of 40 (Revenue Growth % + FCF Margin %)", rule40_lines)
    ).rstrip()

    most_recent = col_date(inc_cols[0])
    return {"text": text, "source": f"{ticker} Computed Metrics",
            "source_type": "computed_metrics", "period": most_recent,
            "form_type": "computed_metrics"}


# ==============================================================================
# STEP 3b — PRE-COMPUTED TREND ANALYSIS
# ==============================================================================

def compute_trend_analysis(name, ticker, data):
    """
    Build one dedicated trend-analysis chunk from the parsed quarterly data:
    per-quarter YoY revenue growth, gross margin, FCF margin and SBC % of
    revenue, plus trend direction (last 2 quarters vs the prior 2) and
    inflection points (the quarter where the QoQ direction flipped).

    Any metric with fewer than 3 quarters of data is labelled INSUFFICIENT
    DATA and its trend/inflection detection is skipped. Best-effort: never
    raises — returns None if nothing useful can be computed.
    """
    try:
        inc = data["income_q"]
        cf  = data["cashflow_q"]
        inc_cols = sorted_cols(inc)
        cf_cols  = sorted_cols(cf)
        if not inc_cols:
            return None

        fye_month = detect_fye_month(data)

        # ---- parse each quarter, sorted chronologically (oldest -> newest) ----
        quarters = []
        for col in sorted(inc_cols):
            period = col_date(col)
            fy, q = fiscal_year_quarter(period, fye_month)
            rev = cell(inc, ["Total Revenue", "Revenue", "Operating Revenue"], col)
            gp  = cell(inc, ["Gross Profit"], col)
            sbc = cell(cf, ["Stock Based Compensation"], col) if col in cf_cols else None
            fcf = cell(cf, ["Free Cash Flow"], col) if col in cf_cols else None
            yoy = None
            pcol = prior_year_col(inc_cols, col)   # same quarter, prior year
            if rev and pcol is not None:
                prev = cell(inc, ["Total Revenue", "Revenue", "Operating Revenue"], pcol)
                if prev:
                    yoy = (rev - prev) / prev * 100
            quarters.append({
                "label":      f"Q{q} FY{fy}",
                "rev_yoy":    yoy,
                "gm":         (gp / rev * 100) if (gp is not None and rev) else None,
                "fcf_margin": (fcf / rev * 100) if (fcf is not None and rev) else None,
                "sbc_pct":    (sbc / rev * 100) if (sbc is not None and rev) else None,
            })

        def metric_block(title, noun, key, fmt):
            """One formatted section: per-quarter series, direction, inflection."""
            pts = [(qq["label"], qq[key]) for qq in quarters if qq[key] is not None]
            if len(pts) < 3:
                return (f"{title}:\n"
                        f"  INSUFFICIENT DATA ({len(pts)} quarter(s) available — need at least 3).\n")
            vals = [v for _, v in pts]
            series = " → ".join(f"{lab}: {fmt(v)}" for lab, v in pts)

            # Direction: average of the last 2 quarters vs the prior 2
            # (the prior group shrinks to 1 quarter when only 3 exist).
            recent = sum(vals[-2:]) / 2
            prior_grp = vals[-4:-2] if len(vals) >= 4 else vals[:-2]
            delta = recent - sum(prior_grp) / len(prior_grp)
            if abs(delta) <= 1.0:
                direction = f"STABLE (within 1%; range: {fmt(min(vals))} to {fmt(max(vals))})"
            elif delta > 0:
                direction = f"ACCELERATING ({delta:+.1f}pp, last 2 quarters vs prior 2)"
            else:
                direction = f"DECELERATING ({delta:+.1f}pp, last 2 quarters vs prior 2)"

            # Inflection: the most recent quarter where the QoQ direction
            # flipped sign (e.g. quarters of deceleration, then acceleration).
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

        # ---- analyst consensus trend (best-effort; format varies by yf ver) ----
        consensus = "ANALYST CONSENSUS TREND:\n  Not available.\n"
        try:
            rec = data.get("recommendations")
            if rec is not None and not getattr(rec, "empty", True) \
                    and "period" in getattr(rec, "columns", []):
                rows = {str(r.get("period")): r for _, r in rec.iterrows()}

                def counts(r):
                    return (f"{int(fnum(r.get('strongBuy')) or 0)} strong buy / "
                            f"{int(fnum(r.get('buy')) or 0)} buy / "
                            f"{int(fnum(r.get('hold')) or 0)} hold / "
                            f"{int(fnum(r.get('sell')) or 0)} sell / "
                            f"{int(fnum(r.get('strongSell')) or 0)} strong sell")

                if "0m" in rows and "-3m" in rows:
                    consensus = ("ANALYST CONSENSUS TREND:\n"
                                 f"  3 months ago: {counts(rows['-3m'])}\n"
                                 f"  Now:          {counts(rows['0m'])}\n")
        except Exception:
            pass  # keep the 'Not available.' default

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

        most_recent = col_date(inc_cols[0])
        return {"text": text, "source": f"{name} Trend Analysis",
                "source_type": "trend_analysis", "period": most_recent,
                "form_type": "trend_analysis"}
    except Exception as e:
        print(f"   ⚠️  Warning: trend analysis failed ({e}) — continuing without it.")
        return None


# ==============================================================================
# STEP 4 — SEC EDGAR PULL  (unchanged)
# ==============================================================================

def html_to_text(raw):
    """Very small HTML -> text: strip script/style, drop tags, unescape."""
    raw = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</p>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n\s*\n+", "\n\n", raw)
    return raw.strip()


def extract_item(text, start_pats, end_pats, cap):
    """
    Slice from the first start-pattern match to the first end-pattern match
    after it. Returns capped text, or None if not found.
    """
    lower = text.lower()
    start = -1
    for pat in start_pats:
        m = re.search(pat, lower)
        if m:
            # prefer the LAST occurrence (TOC lists items before the real body)
            for mm in re.finditer(pat, lower):
                start = mm.start()
            break
    if start == -1:
        return None

    end = len(text)
    for pat in end_pats:
        m = re.search(pat, lower[start + 50:])
        if m:
            end = min(end, start + 50 + m.start())
    section = text[start:end].strip()
    if len(section) < 200:
        return None
    return section[:cap]


def resolve_cik(ticker):
    """Map ticker -> zero-padded 10-digit CIK via SEC's ticker file."""
    try:
        resp = requests.get(SEC_TICKERS_URL, headers=SEC_UA, timeout=30)
        resp.raise_for_status()
        for row in resp.json().values():
            if (row.get("ticker") or "").upper() == ticker.upper():
                return str(row["cik_str"]).zfill(10)
    except Exception as e:
        print(f"   ⚠️  Warning: could not resolve CIK ({e})")
    return None


def pull_sec(name, ticker):
    """Return a list of SEC chunks (MD&A / Risk Factors / Business). Never raises."""
    chunks = []
    print("\n  📁 Pulling SEC filings...")

    cik = resolve_cik(ticker)
    if not cik:
        print(f"   ⚠️  Warning: no CIK for {ticker} — skipping SEC, continuing.")
        return chunks

    try:
        sub = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json",
                           headers=SEC_UA, timeout=30).json()
    except Exception as e:
        print(f"   ⚠️  Warning: could not load SEC submissions ({e}) — skipping SEC.")
        return chunks

    recent = sub.get("filings", {}).get("recent", {})
    forms   = recent.get("form", [])
    accns   = recent.get("accessionNumber", [])
    primary = recent.get("primaryDocument", [])
    rdates  = recent.get("reportDate", [])

    def collect(form_wanted, limit):
        out = []
        for i, f in enumerate(forms):
            if f == form_wanted:
                out.append(i)
            if len(out) >= limit:
                break
        return out

    targets = [("10-Q", i) for i in collect("10-Q", 4)] + [("10-K", i) for i in collect("10-K", 1)]
    if not targets:
        print("   ⚠️  Warning: no 10-Q/10-K filings found — continuing.")
        return chunks

    cik_int = str(int(cik))  # strip leading zeros for archive path
    for form, idx in targets:
        rdate = rdates[idx] if idx < len(rdates) else "?"
        accn  = accns[idx].replace("-", "")
        doc   = primary[idx] if idx < len(primary) else ""
        url   = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn}/{doc}"
        try:
            time.sleep(0.3)  # be polite to EDGAR
            raw = requests.get(url, headers=SEC_UA, timeout=40).text
            text = html_to_text(raw)
        except Exception as e:
            print(f"   ⚠️  Warning: could not pull {form} ({rdate}): {e} — continuing.")
            continue

        got = []

        # MD&A — Item 2 (10-Q) or Item 7 (10-K)
        if form == "10-Q":
            mdna = extract_item(text,
                                [r"item\s*2\.?\s*management.s discussion"],
                                [r"item\s*3\.?\s", r"item\s*4\.?\s"], CAP_MDNA)
        else:
            mdna = extract_item(text,
                                [r"item\s*7\.?\s*management.s discussion"],
                                [r"item\s*7a\.?\s", r"item\s*8\.?\s"], CAP_MDNA)
        if mdna:
            chunks.append({"text": f"{name} ({ticker}) — {form} MD&A ({rdate})\n\n{mdna}",
                           "source": f"{ticker} {form} MD&A ({rdate})",
                           "source_type": "sec_filing", "period": rdate,
                           "form_type": f"{form} (MD&A)"})
            got.append("MD&A")

        # Risk Factors — Item 1A
        risk = extract_item(text,
                            [r"item\s*1a\.?\s*risk factors"],
                            [r"item\s*1b\.?\s", r"item\s*2\.?\s", r"item\s*3\.?\s"], CAP_RISK)
        if risk:
            chunks.append({"text": f"{name} ({ticker}) — {form} Risk Factors ({rdate})\n\n{risk}",
                           "source": f"{ticker} {form} Risk Factors ({rdate})",
                           "source_type": "sec_filing", "period": rdate,
                           "form_type": f"{form} (Risk Factors)"})
            got.append("Risk Factors")

        # Business — Item 1 (10-K only)
        if form == "10-K":
            biz = extract_item(text,
                               [r"item\s*1\.?\s*business"],
                               [r"item\s*1a\.?\s"], CAP_BIZ)
            if biz:
                chunks.append({"text": f"{name} ({ticker}) — 10-K Business ({rdate})\n\n{biz}",
                               "source": f"{ticker} 10-K Business ({rdate})",
                               "source_type": "sec_filing", "period": rdate,
                               "form_type": "10-K (Business)"})
                got.insert(0, "Business")

        status = " + ".join(got) if got else "⚠️ no sections extracted"
        print(f"   {form} ({rdate})   {'✅ ' + status if got else status}")

    return chunks


# ==============================================================================
# STEP 5 — CHROMADB INGESTION  (unchanged)
# ==============================================================================

def ingest(chunks, company_key, ticker, exchange, append):
    """Embed and load chunks into company_financials. Returns (added, total)."""
    if not chunks:
        print("\n  ERROR: no chunks were built — nothing to ingest.")
        sys.exit(1)

    iso = datetime.now().isoformat(timespec="seconds")

    texts, metadatas, ids = [], [], []
    for n, c in enumerate(chunks):
        texts.append(c["text"])
        metadatas.append({
            "company":     company_key,
            "ticker":      ticker,
            "exchange":    exchange,
            "source":      c["source"],
            "source_type": c["source_type"],
            "period":      c.get("period", "Unknown"),
            "form_type":   c.get("form_type", "Unknown"),
            "ingested_at": iso,
        })
        safe = re.sub(r"[^a-zA-Z0-9]", "_", f"{ticker}_{c['source_type']}_{c.get('period','')}")[:60]
        ids.append(f"{safe}_{n}")

    # Imported here (not at module top) so torch only loads when embedding is
    # actually needed — keeps --audit and the data-pull phase torch-free.
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        sys.exit("ERROR: 'sentence-transformers' is not installed.")

    print(f"\n  Loading embedding model: {EMBED_MODEL}")
    model = SentenceTransformer(EMBED_MODEL)
    print(f"  Embedding {len(texts):,} chunks...")
    embeddings = model.encode(texts, show_progress_bar=True, batch_size=32)

    print("  Connecting to ChromaDB...")
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    existing = [c.name for c in client.list_collections()]

    if not append:
        if COMPANY_COLLECTION in existing:
            print(f"  Wiping existing '{COMPANY_COLLECTION}' collection...")
            client.delete_collection(COMPANY_COLLECTION)
        collection = client.create_collection(name=COMPANY_COLLECTION,
                                               metadata={"hnsw:space": "cosine"})
    else:
        if COMPANY_COLLECTION in existing:
            collection = client.get_collection(COMPANY_COLLECTION)
            print(f"  Appending — {collection.count():,} chunks already stored.")
        else:
            collection = client.create_collection(name=COMPANY_COLLECTION,
                                                   metadata={"hnsw:space": "cosine"})

    BATCH = 100
    for i in range(0, len(texts), BATCH):
        j = min(i + BATCH, len(texts))
        collection.add(embeddings=embeddings[i:j].tolist(),
                       documents=texts[i:j], metadatas=metadatas[i:j], ids=ids[i:j])
        print(f"  Loaded {j:,} / {len(texts):,} chunks")

    return len(texts), collection.count()


# ==============================================================================
# AUDIT — --audit <company>   (unchanged)
# ==============================================================================

def list_companies():
    """Print every distinct company key stored in company_financials, with counts.
    Lets you confirm the exact name to pass to --company / --audit. Torch-free."""
    from collections import Counter
    bar = "═" * 56
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    existing = [c.name for c in client.list_collections()]

    print(f"\n{bar}")
    print(f"COMPANIES IN '{COMPANY_COLLECTION}'")
    print(bar)

    if COMPANY_COLLECTION not in existing:
        print("(collection does not exist yet — nothing ingested)")
        print(bar)
        return

    coll = client.get_collection(COMPANY_COLLECTION)
    metas = coll.get(include=["metadatas"]).get("metadatas") or []
    if not metas:
        print("(collection is empty)")
        print(bar)
        return

    counts = Counter(m.get("company") for m in metas)
    for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0]))):
        ticker = next((m.get("ticker") for m in metas if m.get("company") == name and m.get("ticker")), "")
        tag = f"  ({ticker})" if ticker else ""
        print(f"   {n:>4} chunks   {name}{tag}")
    print(bar)
    print("Pass the exact name above to --company (debates) or --audit (detail).")


def run_audit(company_arg):
    company_key = normalize_company(company_arg)
    bar = "═" * 56

    client = chromadb.PersistentClient(path=CHROMA_DIR)
    existing = [c.name for c in client.list_collections()]
    if COMPANY_COLLECTION not in existing:
        print(f"\n  '{COMPANY_COLLECTION}' collection does not exist yet. Nothing to audit.")
        return

    coll = client.get_collection(COMPANY_COLLECTION)
    res = coll.get(where={"company": company_key}, include=["documents", "metadatas"])
    metas = res.get("metadatas") or []
    docs  = res.get("documents") or []

    print(f"\n{bar}")
    print(f"COMPANY FINANCIALS AUDIT — {company_arg}")
    print(bar)

    if not metas:
        avail = sorted({m.get("company") for m in (coll.get(include=["metadatas"]).get("metadatas") or [])})
        print(f"No chunks found for '{company_key}'.")
        if avail:
            print(f"Companies currently in the collection: {', '.join(a for a in avail if a)}")
        print(bar)
        return

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
    inc = [(m, d) for m, d in zip(metas, docs) if m.get("form_type") == "income_statement"]
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
    tenq_mdna = count_form("10-Q (MD&A)")
    tenk      = has_form_contains("10-K")
    risk      = has_form_contains("Risk Factors")

    print("\nCOVERAGE CHECK:")
    print(f"   {mark(inc_n>0)} Income statements    : {inc_n} quarters")
    print(f"   {mark(bal_n>0)} Balance sheets       : {bal_n} quarters")
    print(f"   {mark(cf_n>0)} Cash flow            : {cf_n} quarters")
    print(f"   {mark(has_type('computed_metrics'))} Computed metrics     : {'present' if has_type('computed_metrics') else 'missing'}")
    print(f"   {mark(tenq_mdna>0)} SEC 10-Q filings     : {tenq_mdna} quarters")
    print(f"   {mark(tenk)} SEC 10-K filing      : {'present' if tenk else 'missing'}")
    print(f"   {mark(risk)} SEC Risk Factors     : {'present' if risk else 'missing'}")
    print(bar)


# ==============================================================================
# MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Auto-ingest a company's financials (yfinance + SEC EDGAR).")
    parser.add_argument("--ticker",   type=str, help="Ticker symbol, e.g. MDB")
    parser.add_argument("--exchange", type=str, default=None,
                        help="Exchange, e.g. NASDAQ (display/metadata only — yfinance fetches by ticker)")
    parser.add_argument("--append",   action="store_true", help="Add alongside existing company data instead of wiping")
    parser.add_argument("--audit",    type=str, default=None, metavar="COMPANY",
                        help="Audit what is already ingested for COMPANY, then exit")
    parser.add_argument("--list",      action="store_true",
                        help="List every company stored in company_financials (and exit)")
    args = parser.parse_args()

    # ── List mode: just show what's stored, then exit (no torch, no network) ──
    if args.list:
        list_companies()
        return

    # ── Audit mode: no ingestion, no network needed ──
    if args.audit:
        run_audit(args.audit)
        return

    if not args.ticker:
        print("\n  ERROR: --ticker is required (or use --audit <company>).")
        sys.exit(1)

    start = datetime.now()
    ticker = args.ticker.upper()

    # STEP 1 — validate ticker via yfinance .info (no API key)
    print(f"\n  Validating ticker '{ticker}' via yfinance...")
    ticker_obj = make_ticker(ticker)
    try:
        info = ticker_obj.info or {}
    except Exception as e:
        print(f"\n  ERROR: yfinance could not load '{ticker}' ({e}).")
        sys.exit(1)

    if not ticker_is_valid(info):
        print(f"\n  ERROR: ticker '{ticker}' not found on yfinance (empty info).")
        print(f"  Check the symbol and try again.")
        sys.exit(1)

    resolved = resolve_ticker(ticker, info, args.exchange)
    exchange = resolved["exchange"]

    # STEP 2 — yfinance pull
    data = pull_yf(ticker_obj, info)

    display = resolved["name"] or ticker

    # company KEY used for storage + retrieval (matches main.py's normalize_company)
    clean = re.sub(r",?\s+(inc\.?|incorporated|corp\.?|corporation|co\.?|ltd\.?|plc|holdings|group)\b",
                   "", display, flags=re.IGNORECASE)
    clean = clean.strip().rstrip(".,").strip()   # drop any trailing period/comma ("MongoDB." -> "MongoDB")
    company_key = normalize_company(clean)

    if ncols(data["income_q"]) == 0 and ncols(data["income_a"]) == 0:
        print("\n  ERROR: yfinance returned no income statement data for this ticker.")
        print("  Check the ticker, or try again later (yfinance can rate-limit).")
        sys.exit(1)

    market_chunks = build_market_chunks(display, ticker, data)

    # STEP 3 — computed
    computed = build_computed_chunk(display, ticker, data)
    computed_chunks = [computed] if computed else []

    # STEP 3b — pre-computed trend analysis (directions + inflection points)
    trend = compute_trend_analysis(display, ticker, data)
    trend_chunks = [trend] if trend else []

    # STEP 4 — SEC (best-effort, never blocks)
    try:
        sec_chunks = pull_sec(display, ticker)
    except Exception as e:
        print(f"   ⚠️  Warning: SEC pull failed entirely ({e}) — continuing with yfinance data.")
        sec_chunks = []

    all_chunks = market_chunks + computed_chunks + trend_chunks + sec_chunks

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
    print(f"   yfinance financials : {count('yfinance_financials'):>3} chunks")
    print(f"   yfinance metrics    : {count('yfinance_metrics'):>3} chunks")
    print(f"   Computed metrics    : {count('computed_metrics'):>3} chunks")
    print(f"   Trend analysis      : {trend_count:>3} chunk{'s' if trend_count != 1 else ''}")
    print(f"   SEC filings         : {count('sec_filing'):>3} chunks")
    print("   ──────────────────────────────")
    print(f"   Total added         : {added:>3} chunks")
    print(f"   Collection          : {COMPANY_COLLECTION} ({total} total docs)")
    print(f"   Stored as           : company='{company_key}'  (use this with --company)")
    print(f"   Time taken          : {elapsed:.1f}s")
    print(bar)
    print(f"\nRun audit to verify:")
    print(f"   py -3.11 scripts/analyse_company.py --audit {company_key}")
    print(f"\nReady to debate:")
    print(f'   py -3.11 main.py --topic "Is {display} a good investment?" --company {company_key} --agents buffett howard_marks')


if __name__ == "__main__":
    main()
