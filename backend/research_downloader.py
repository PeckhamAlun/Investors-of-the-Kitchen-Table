"""
research_downloader.py — research-package download logic for the TIKT backend.

Fetches a company's primary-source research (SEC 10-K / 10-Q filings for US
companies, FMP financial statements for non-US companies, plus the last 12
quarters of earnings-call transcripts) and assembles them into a single ZIP of
PDFs, streamed back to the frontend by main.py.

Standalone module — it does NOT import from backend/main.py. The only thing it
needs from the caller is the FMP API key, passed in as a parameter.

Data sources
------------
  * SEC EDGAR  — company_tickers.json (ticker -> CIK), submissions API (filings
                 list), and the filing archives (pre-rendered .pdf if present,
                 else the primary .htm converted with WeasyPrint).
  * FMP        — /stable financial statements and earnings-call transcripts
                 (same endpoints / logic as scripts/analyse_company.py).

Networking
----------
  * SEC   — `requests` (sync); the async orchestrator runs these in an executor.
  * FMP   — `httpx.AsyncClient` (async).
  * PDFs  — ReportLab for transcripts / financial statements; WeasyPrint for
            SEC HTM -> PDF conversion.
  * ZIP   — zipfile + io.BytesIO, entirely in memory.
"""

import io
import re
import html
import zipfile
import asyncio
from datetime import datetime, date

import httpx
import requests

# SEC requires a descriptive User-Agent on every request or it returns 403.
SEC_HEADERS = {"User-Agent": "TIKT Research tikt@research.com"}
SEC_TIMEOUT = 60

FMP_BASE = "https://financialmodelingprep.com/stable"

# In-memory cache of SEC's company_tickers.json (fetched once per process).
_TICKERS_CACHE = None


# ==============================================================================
# 1. TICKER -> CIK  (SEC company_tickers.json, cached in memory)
# ==============================================================================

def _fetch_company_tickers():
    """Fetch and memoise SEC's ticker->CIK map. Returns the parsed dict (keyed by
    a numeric string index, each value {cik_str, ticker, title})."""
    global _TICKERS_CACHE
    if _TICKERS_CACHE is None:
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=SEC_HEADERS,
            timeout=SEC_TIMEOUT,
        )
        resp.raise_for_status()
        _TICKERS_CACHE = resp.json()
    return _TICKERS_CACHE


def get_cik(ticker: str) -> str | None:
    """Resolve a ticker to its zero-padded 10-digit SEC CIK, or None if the ticker
    isn't in SEC's list (i.e. a non-US company). Case-insensitive match."""
    try:
        data = _fetch_company_tickers()
    except Exception:
        return None

    want = ticker.strip().upper()
    for entry in data.values():
        if str(entry.get("ticker", "")).upper() == want:
            return str(entry.get("cik_str")).zfill(10)
    return None


# ==============================================================================
# 2. FILINGS LIST  (SEC submissions API)
# ==============================================================================

def get_sec_filings(cik: str, form_type: str, years: int) -> list[dict]:
    """Return the company's filings of `form_type` (e.g. "10-K" / "10-Q") from the
    last `years` years, newest-first. Each item: {accession, date, primary_doc}.

    Reads the SEC submissions API, which returns the ~1000 most recent filings in
    `filings.recent` (plenty for a few years of 10-K/10-Q). Returns [] on error."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    try:
        resp = requests.get(url, headers=SEC_HEADERS, timeout=SEC_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    recent = (data.get("filings") or {}).get("recent") or {}
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])

    # Cutoff = today minus N years (guard the Feb-29 -> non-leap-year edge case).
    today = date.today()
    try:
        cutoff = today.replace(year=today.year - years)
    except ValueError:
        cutoff = today.replace(year=today.year - years, day=28)

    results = []
    for i in range(len(accessions)):
        if i >= len(forms) or forms[i] != form_type:
            continue
        filing_date = dates[i] if i < len(dates) else ""
        try:
            fd = datetime.strptime(filing_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        if fd < cutoff:
            continue
        results.append({
            "accession": accessions[i],
            "date": filing_date,
            "primary_doc": primary_docs[i] if i < len(primary_docs) else "",
        })

    # SEC returns newest-first already, but sort defensively.
    results.sort(key=lambda r: r["date"], reverse=True)
    return results


# ==============================================================================
# 3. FILING -> PDF BYTES  (pre-rendered PDF if present, else HTM via WeasyPrint)
# ==============================================================================

def download_filing_as_pdf(
    cik: str, accession: str, primary_doc: str, date: str, form_type: str
) -> bytes | None:
    """Return a single SEC filing as PDF bytes. If the filing directory already
    contains a rendered .pdf, download and return it directly; otherwise fetch the
    primary .htm document and convert it to PDF with WeasyPrint. Returns None on
    any failure (unresolved doc, network error, conversion error)."""
    try:
        acc_nodash = accession.replace("-", "")
        # int(cik) strips the zero-padding — the archives path uses the bare CIK.
        base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc_nodash}/"

        # Look for a pre-rendered PDF in the filing index.
        pdf_name = None
        try:
            idx = requests.get(f"{base}index.json", headers=SEC_HEADERS, timeout=SEC_TIMEOUT)
            idx.raise_for_status()
            items = (idx.json().get("directory") or {}).get("item") or []
            for it in items:
                name = it.get("name", "")
                if name.lower().endswith(".pdf"):
                    pdf_name = name
                    break
        except Exception:
            pdf_name = None

        if pdf_name:
            resp = requests.get(f"{base}{pdf_name}", headers=SEC_HEADERS, timeout=SEC_TIMEOUT)
            resp.raise_for_status()
            return resp.content

        # No rendered PDF — fetch the primary HTM and convert with WeasyPrint.
        if not primary_doc:
            return None
        resp = requests.get(f"{base}{primary_doc}", headers=SEC_HEADERS, timeout=SEC_TIMEOUT)
        resp.raise_for_status()

        from weasyprint import HTML
        # base_url lets WeasyPrint resolve the filing's relative CSS/image links.
        return HTML(string=resp.text, base_url=base).write_pdf()
    except Exception:
        return None


# ==============================================================================
# 4. TRANSCRIPT -> PDF BYTES  (ReportLab)
# ==============================================================================

def _page_footer(canvas, doc):
    """Draw a centred 'Page N' footer on every page."""
    from reportlab.lib import colors
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.grey)
    width, _height = canvas._pagesize
    canvas.drawCentredString(width / 2.0, 36, f"Page {doc.page}")
    canvas.restoreState()


def transcript_to_pdf(
    ticker: str, quarter: int, year: int, date: str, content: str
) -> bytes:
    """Render an earnings-call transcript into a clean PDF (bytes): bold header,
    muted date line, horizontal rule, 10pt wrapped body, page numbers."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.9 * inch,
        rightMargin=0.9 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.9 * inch,
        title=f"{ticker} Earnings Call Q{quarter} {year}",
    )

    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        "TranscriptHeader", parent=styles["Title"],
        fontSize=18, leading=22, spaceAfter=4, alignment=TA_LEFT,
    )
    date_style = ParagraphStyle(
        "TranscriptDate", parent=styles["Normal"],
        fontSize=10, textColor=colors.grey, spaceAfter=8,
    )
    body_style = ParagraphStyle(
        "TranscriptBody", parent=styles["Normal"],
        fontSize=10, leading=14, spaceAfter=6, alignment=TA_LEFT,
    )

    story = [Paragraph(f"{ticker} Earnings Call Q{quarter} {year}", header_style)]
    if date:
        story.append(Paragraph(str(date), date_style))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.grey,
                            spaceBefore=2, spaceAfter=10))

    # One Paragraph per line (ReportLab wraps + splits each across pages as needed);
    # escape XML-special chars so ampersands/angle brackets don't break rendering.
    for line in (content or "").replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if not line:
            story.append(Spacer(1, 4))
            continue
        story.append(Paragraph(html.escape(line), body_style))

    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buf.getvalue()


# ==============================================================================
# FINANCIAL-STATEMENTS PDF  (non-US fallback — FMP data, ReportLab tables)
# ==============================================================================

def _fnum(v):
    """Safe float, or None (treats '' and NaN as None)."""
    try:
        if v is None or v == "":
            return None
        f = float(v)
        return None if f != f else f
    except (TypeError, ValueError):
        return None


def _money(v):
    """Human-readable money: -$32.1M, $1.2B, $529.4M, $940. 'N/A' if missing."""
    v = _fnum(v)
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


def _plain_num(v):
    """Plain 2-dp number (for EPS and similar), or 'N/A'."""
    v = _fnum(v)
    return "N/A" if v is None else f"{v:,.2f}"


# Line items rendered per statement: (row label, FMP field, formatter).
_INCOME_ROWS = [
    ("Revenue", "revenue", _money),
    ("Gross Profit", "grossProfit", _money),
    ("Operating Income", "operatingIncome", _money),
    ("Net Income", "netIncome", _money),
    ("EPS (Diluted)", "epsdiluted", _plain_num),
]
_BALANCE_ROWS = [
    ("Cash & Equivalents", "cashAndCashEquivalents", _money),
    ("Total Current Assets", "totalCurrentAssets", _money),
    ("Total Assets", "totalAssets", _money),
    ("Total Debt", "totalDebt", _money),
    ("Total Liabilities", "totalLiabilities", _money),
    ("Total Stockholders' Equity", "totalStockholdersEquity", _money),
]
_CASHFLOW_ROWS = [
    ("Operating Cash Flow", "operatingCashFlow", _money),
    ("Capital Expenditure", "capitalExpenditure", _money),
    ("Free Cash Flow", "freeCashFlow", _money),
    ("Stock-Based Compensation", "stockBasedCompensation", _money),
]


def _statement_table(title, rows, records):
    """Build a ReportLab Table for one statement: line items down the left,
    fiscal years across the top (newest-first, capped at 5 columns)."""
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    records = records[:5]
    years = [
        str(r.get("fiscalYear") or r.get("calendarYear") or (r.get("date", "") or "")[:4] or "?")
        for r in records
    ]
    data = [[title] + years]
    for label, key, fmt in rows:
        data.append([label] + [fmt(r.get(key)) for r in records])

    table = Table(data, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#d1d5db")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _financials_pdf_bytes(ticker, income, balance, cashflow) -> bytes:
    """Render FMP income / balance / cash-flow statements into one PDF (bytes)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.9 * inch,
        bottomMargin=0.9 * inch,
        title=f"{ticker} Financial Statements",
    )

    styles = getSampleStyleSheet()
    header_style = ParagraphStyle(
        "FinHeader", parent=styles["Title"],
        fontSize=18, leading=22, spaceAfter=4, alignment=TA_LEFT,
    )
    sub_style = ParagraphStyle(
        "FinSub", parent=styles["Normal"],
        fontSize=10, textColor=colors.grey, spaceAfter=8,
    )
    section_style = ParagraphStyle(
        "FinSection", parent=styles["Heading2"],
        fontSize=13, spaceBefore=14, spaceAfter=6,
    )

    story = [
        Paragraph(f"{ticker} Financial Statements", header_style),
        Paragraph("Annual statements — source: Financial Modeling Prep", sub_style),
        HRFlowable(width="100%", thickness=1, color=colors.grey, spaceBefore=2, spaceAfter=8),
    ]

    for title, rows, records in (
        ("Income Statement", _INCOME_ROWS, income),
        ("Balance Sheet", _BALANCE_ROWS, balance),
        ("Cash Flow Statement", _CASHFLOW_ROWS, cashflow),
    ):
        if not records:
            continue
        story.append(Paragraph(title, section_style))
        story.append(_statement_table(title, rows, records))
        story.append(Spacer(1, 8))

    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buf.getvalue()


# ==============================================================================
# FMP FETCH HELPERS (async)
# ==============================================================================

async def _fetch_financials(ticker: str, fmp_api_key: str):
    """Fetch annual income / balance / cash-flow statements from FMP (last 5 years).
    Returns a financial-statements PDF (bytes), or None if nothing came back."""
    params = {"symbol": ticker, "period": "annual", "limit": 5, "apikey": fmp_api_key}
    try:
        async with httpx.AsyncClient() as client:
            inc_r, bal_r, cf_r = await asyncio.gather(
                client.get(f"{FMP_BASE}/income-statement", params=params, timeout=30),
                client.get(f"{FMP_BASE}/balance-sheet-statement", params=params, timeout=30),
                client.get(f"{FMP_BASE}/cash-flow-statement", params=params, timeout=30),
            )
    except Exception:
        return None

    def _as_list(resp):
        try:
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception:
            return []

    income, balance, cashflow = _as_list(inc_r), _as_list(bal_r), _as_list(cf_r)
    if not (income or balance or cashflow):
        return None
    return _financials_pdf_bytes(ticker, income, balance, cashflow)


async def _fetch_transcripts(ticker: str, fmp_api_key: str) -> list[dict]:
    """Fetch the last 12 quarters of FMP earnings-call transcripts (same period
    logic as analyse_company.py). Returns [{quarter, year, date, content}, ...],
    skipping quarters with no transcript. Best-effort — never raises."""
    now = datetime.now()
    y, q = now.year, (now.month - 1) // 3 + 1
    periods = []
    for _ in range(12):
        periods.append((y, q))
        q -= 1
        if q == 0:
            q, y = 4, y - 1

    async with httpx.AsyncClient() as client:
        async def fetch_one(year, quarter):
            try:
                resp = await client.get(
                    f"{FMP_BASE}/earning-call-transcript",
                    params={"symbol": ticker, "year": year,
                            "quarter": quarter, "apikey": fmp_api_key},
                    timeout=30,
                )
                resp.raise_for_status()
                payload = resp.json()
            except Exception:
                return None
            if not payload or not isinstance(payload, list):
                return None
            record = payload[0] or {}
            content = (record.get("content") or "").strip()
            if not content:
                return None
            return {"quarter": quarter, "year": year,
                    "date": record.get("date", ""), "content": content}

        fetched = await asyncio.gather(*[fetch_one(yr, qr) for yr, qr in periods])

    return [f for f in fetched if f]


# ==============================================================================
# 5. ORCHESTRATOR — build the research ZIP
# ==============================================================================

async def build_research_package(
    ticker: str,
    years: int,
    fmp_api_key: str,
    progress_callback,  # callable(message: str)
) -> bytes:
    """Build the full research package for `ticker` and return it as a ZIP (bytes).

    For US companies (found in SEC EDGAR): downloads the last `years` years of
    10-K and 10-Q filings as PDFs. For non-US companies: builds a financial-
    statements PDF from FMP instead. In both cases it also adds the last 12
    quarters of earnings-call transcripts as PDFs.

    `progress_callback(message)` is invoked at each step so the FastAPI endpoint
    can stream progress to the frontend."""
    ticker = ticker.upper()
    loop = asyncio.get_running_loop()
    files: dict[str, bytes] = {}  # zip path -> pdf bytes

    def progress(message: str):
        if progress_callback:
            try:
                progress_callback(message)
            except Exception:
                pass

    # a. Resolve ticker -> CIK (SEC call; sync -> executor).
    progress("Looking up company in SEC EDGAR...")
    cik = await loop.run_in_executor(None, get_cik, ticker)

    if cik:
        # b. US company — pull 10-K then 10-Q filings.
        progress("Fetching 10-K filings...")
        for f in await loop.run_in_executor(None, get_sec_filings, cik, "10-K", years):
            fdate = f["date"]
            progress(f"Downloading 10-K {fdate}...")
            pdf = await loop.run_in_executor(
                None, download_filing_as_pdf,
                cik, f["accession"], f["primary_doc"], fdate, "10-K",
            )
            if pdf:
                files[f"{ticker}/10-K/{ticker}_10-K_{fdate}.pdf"] = pdf

        progress("Fetching 10-Q filings...")
        for f in await loop.run_in_executor(None, get_sec_filings, cik, "10-Q", years):
            fdate = f["date"]
            progress(f"Downloading 10-Q {fdate}...")
            pdf = await loop.run_in_executor(
                None, download_filing_as_pdf,
                cik, f["accession"], f["primary_doc"], fdate, "10-Q",
            )
            if pdf:
                files[f"{ticker}/10-Q/{ticker}_10-Q_{fdate}.pdf"] = pdf
    else:
        # c. Non-US company — no SEC filings; fall back to FMP financials PDF.
        progress("Non-US company — fetching FMP financials...")
        fin_pdf = await _fetch_financials(ticker, fmp_api_key)
        if fin_pdf:
            files[f"{ticker}/financials/{ticker}_Financials.pdf"] = fin_pdf

    # d. Earnings-call transcripts (FMP; async) — for US and non-US alike.
    progress("Fetching earnings call transcripts...")
    for t in await _fetch_transcripts(ticker, fmp_api_key):
        content = (t.get("content") or "").strip()
        if not content:
            continue
        q, yr, tdate = t["quarter"], t["year"], t.get("date", "")
        pdf = await loop.run_in_executor(
            None, transcript_to_pdf, ticker, q, yr, tdate, content,
        )
        files[f"{ticker}/transcripts/{ticker}_Q{q}_{yr}_Transcript.pdf"] = pdf
        progress(f"Transcript Q{q} {yr} ✓")

    # e. Assemble the ZIP in memory.
    progress("Assembling ZIP...")
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return zip_buffer.getvalue()
