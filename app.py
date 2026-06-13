"""
==============================================================================
  APP.PY — THE KITCHEN TABLE (Streamlit web UI)
  Professional financial-SaaS front-end for the multi-agent equity debate
  engine. White canvas, dark navy sidebar, #2563EB blue accent.

  Three views (st.session_state["view"]):
    home    — hero search + agent preview
    company — auto-ingest by ticker, header card, metrics, chart, quarterlies,
              and the "Sit at the Kitchen Table" debate launcher
    debate  — live debate output (turns stream in as agents speak) + PDF

  RUN:
      streamlit run app.py

  Reuses the debate engine directly (no duplication): run_round / save_pdf /
  display_name come from main.py; all paths, names and registry from config.py.
==============================================================================
"""

import os
import re
import sys
import html
import subprocess
from datetime import datetime

import streamlit as st

# ── Page config must be the first Streamlit call ──
st.set_page_config(
    page_title="The Kitchen Table",
    page_icon="🪑",
    layout="wide",
    initial_sidebar_state="expanded",
)

import yfinance as yf
from pymongo import MongoClient
from pymongo.server_api import ServerApi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Importing main initialises the engine's shared clients (Mongo, Gemini,
# Anthropic) once per Streamlit process — exactly what the debate needs.
import main as engine
from main import run_round, save_pdf, display_name
from config import (MONGODB_URI, MONGODB_DB_NAME, MONGO_COMPANY_COLLECTION,
                    AGENT_REGISTRY, AGENT_DISPLAY, AGENT_COLOURS,
                    normalize_company, system_prompt_path)

mongo_client = MongoClient(MONGODB_URI, server_api=ServerApi('1'))
db = mongo_client[MONGODB_DB_NAME]

ROOT = os.path.dirname(os.path.abspath(__file__))

# Agents selectable in the UI = registry entries that actually have a system
# prompt on disk (excludes placeholders like munger until they're built).
ACTIVE_AGENTS = [a for a in AGENT_REGISTRY if os.path.exists(system_prompt_path(a))]

BLUE       = "#2563EB"
BLUE_LIGHT = "#EFF6FF"
NAVY       = "#0F172A"
BORDER     = "#E2E8F0"
MUTED      = "#64748B"
GREEN      = "#16A34A"
RED        = "#DC2626"

PERIOD_MAP = {"1M": "1mo", "3M": "3mo", "6M": "6mo", "1Y": "1y"}


# ==============================================================================
# SESSION STATE
# ==============================================================================

_DEFAULTS = {
    "view":           "home",     # "home" | "company" | "debate"
    "ticker":         "",
    "company":        "",
    "agents":         [],
    "topic":          "",
    "turns":          2,
    "debate_history": [],
    "pdf_path":       None,
    "recent":         [],
    "chart_period":   "6M",
    "collapsed":      False,
    "debate_done":    False,
}
for k, v in _DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v


def go_to_ticker(ticker: str) -> None:
    """Switch to the company view for a ticker; track recent searches."""
    ticker = ticker.strip().upper()
    if not ticker:
        return
    recent = [t for t in st.session_state["recent"] if t != ticker]
    st.session_state["recent"] = ([ticker] + recent)[:3]
    st.session_state["ticker"] = ticker
    st.session_state["company"] = ""
    st.session_state["view"] = "company"


# ==============================================================================
# GLOBAL CSS
# ==============================================================================

SIDEBAR_WIDTH = "60px" if st.session_state["collapsed"] else "220px"

st.markdown(f"""
<style>
/* ── Canvas ── */
html, body, [class*="css"] {{
    font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
}}
.stApp {{ background: #F8FAFC; }}
.block-container {{ padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1200px; }}
header[data-testid="stHeader"] {{ background: transparent; }}

/* ── Sidebar: dark navy, fixed width ── */
[data-testid="stSidebar"] {{
    background: {NAVY};
    min-width: {SIDEBAR_WIDTH}; max-width: {SIDEBAR_WIDTH};
    border-right: 1px solid #1E293B;
}}
[data-testid="stSidebar"] * {{ color: #F1F5F9; }}
[data-testid="stSidebar"] .stTextInput input {{
    background: #1E293B; color: #F1F5F9; border: 1px solid #334155;
    border-radius: 12px;
}}
[data-testid="stSidebar"] .stTextInput input::placeholder {{ color: #94A3B8; }}
[data-testid="stSidebar"] .stButton button {{
    background: transparent; color: #CBD5E1; border: none;
    border-radius: 8px; width: 100%; text-align: left;
    padding: 0.45rem 0.7rem; font-size: 0.92rem;
}}
[data-testid="stSidebar"] .stButton button:hover {{
    background: #1E293B; color: #FFFFFF;
}}

/* ── Primary buttons: Kitchen Table blue ── */
.stButton button[kind="primary"], .stFormSubmitButton button {{
    background: {BLUE}; color: #FFFFFF; border: none; border-radius: 12px;
    padding: 0.5rem 1.4rem; font-weight: 600;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}}
.stButton button[kind="primary"]:hover, .stFormSubmitButton button:hover {{
    background: #1D4ED8; color: #FFFFFF;
}}
.stDownloadButton button {{
    background: #FFFFFF; color: {BLUE}; border: 1.5px solid {BLUE};
    border-radius: 12px; font-weight: 600;
}}
.stDownloadButton button:hover {{ background: {BLUE_LIGHT}; }}

/* ── Inputs in the main area ── */
.stTextInput input, .stTextArea textarea, .stNumberInput input {{
    border-radius: 12px; border: 1px solid {BORDER}; background: #FFFFFF;
}}

/* ── Reusable card primitives (markdown HTML blocks) ── */
.kt-card {{
    background: #FFFFFF; border: 1px solid {BORDER}; border-radius: 12px;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04); padding: 1.2rem 1.4rem;
    margin-bottom: 1rem;
}}
.kt-eyebrow {{
    color: {BLUE}; text-transform: uppercase; letter-spacing: 0.18em;
    font-size: 0.78rem; font-weight: 700;
}}
.kt-muted {{ color: {MUTED}; font-size: 0.88rem; }}
.kt-chip {{
    display: inline-block; background: #FFFFFF; border: 1px solid {BORDER};
    border-radius: 999px; padding: 0.25rem 0.9rem; margin: 0.15rem;
    font-size: 0.85rem; color: #0F172A;
}}
.kt-badge {{
    display: inline-block; background: {BLUE_LIGHT}; color: {BLUE};
    border-radius: 8px; padding: 0.12rem 0.55rem; font-size: 0.78rem;
    font-weight: 600; margin-right: 0.35rem;
}}
.kt-avatar {{
    display: inline-flex; align-items: center; justify-content: center;
    width: 38px; height: 38px; border-radius: 12px; color: #FFFFFF;
    font-weight: 700; font-size: 0.85rem; margin-right: 0.55rem;
}}

/* ── Expander (Kitchen Table panel): blue accent ── */
div[data-testid="stExpander"] {{
    border: 1.5px solid {BLUE}; border-radius: 12px; background: #FFFFFF;
    box-shadow: 0 1px 3px rgba(0,0,0,0.04);
}}
div[data-testid="stExpander"] summary {{
    background: {BLUE_LIGHT}; border-radius: 12px 12px 0 0; font-weight: 600;
}}

/* ── Checkbox agent cards ── */
.stCheckbox {{ background: #FFFFFF; border-radius: 12px; }}
</style>
""", unsafe_allow_html=True)


# ==============================================================================
# SMALL HELPERS
# ==============================================================================

def hex_colour(agent: str) -> str:
    r, g, b = AGENT_COLOURS.get(agent, (0.2, 0.2, 0.2))
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


def initials(name: str) -> str:
    return "".join(w[0] for w in name.split()[:2]).upper()


def esc(t) -> str:
    return html.escape(str(t))


def fmt_money(v: float | None) -> str:
    if v is None:
        return "N/A"
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"${v / div:,.1f}{suffix}"
    return f"${v:,.0f}"


def pct_to_float(s: str | None) -> float | None:
    """'+30.2%' / '22.1%' / '-5.0%' -> float, else None."""
    if not s:
        return None
    try:
        return float(s.strip().rstrip("%").replace("+", ""))
    except ValueError:
        return None


def trend_arrow(curr: float | None, prev: float | None,
                up_is_good: bool = True) -> str:
    """Coloured ▲/▼ HTML comparing two values ('' if not comparable)."""
    if curr is None or prev is None or curr == prev:
        return ""
    rising = curr > prev
    good = rising if up_is_good else not rising
    colour = GREEN if good else RED
    symbol = "▲" if rising else "▼"
    return f'<span style="color:{colour};font-size:0.8rem;">{symbol}</span>'


# ==============================================================================
# DATA FETCHERS (cached)
# ==============================================================================

@st.cache_data(ttl=300, show_spinner=False)
def fetch_indices() -> list[tuple[str, float, float]]:
    """[(label, last close, % change)] for Dow / S&P 500 / Nasdaq."""
    out = []
    try:
        data = yf.download("^DJI ^GSPC ^IXIC", period="2d", progress=False)
        closes = data["Close"]
        for sym, label in [("^DJI", "Dow Jones"), ("^GSPC", "S&P 500"), ("^IXIC", "Nasdaq")]:
            try:
                series = closes[sym].dropna()
                last, prev = float(series.iloc[-1]), float(series.iloc[-2])
                out.append((label, last, (last - prev) / prev * 100))
            except Exception:
                continue
    except Exception:
        pass
    return out


@st.cache_data(ttl=600, show_spinner=False)
def fetch_info(ticker: str) -> dict:
    try:
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}


@st.cache_data(ttl=600, show_spinner=False)
def fetch_closes(ticker: str, period: str):
    try:
        return yf.download(ticker, period=period, progress=False)["Close"]
    except Exception:
        return None


@st.cache_data(ttl=600, show_spinner=False)
def fetch_next_earnings(ticker: str) -> str | None:
    try:
        cal = yf.Ticker(ticker).calendar
        dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if dates:
            return min(dates).strftime("%d %b %Y")
    except Exception:
        pass
    return None


# ==============================================================================
# MONGODB HELPERS (company data written by scripts/analyse_company.py)
# ==============================================================================

def company_key_for_ticker(ticker: str) -> str | None:
    doc = db[MONGO_COMPANY_COLLECTION].find_one({"ticker": ticker.upper()})
    return doc.get("company") if doc else None


def ensure_company_data(ticker: str) -> str | None:
    """Make sure `ticker` is loaded in company_financials; return its company
    key. Pulls via scripts/analyse_company.py (which wipes by default — the
    engine debates one company at a time) only when the ticker isn't already
    loaded."""
    existing = company_key_for_ticker(ticker)
    if existing:
        return existing

    with st.spinner(f"Pulling financial data for {ticker}... (yfinance + SEC EDGAR, ~1-2 min)"):
        result = subprocess.run(
            ["py", "-3.11", "scripts/analyse_company.py", "--ticker", ticker, "--yes"],
            capture_output=True, text=True,
            cwd=os.path.dirname(os.path.abspath(__file__)),
            encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    if result.returncode != 0:
        st.error(f"Could not ingest **{ticker}** — analyse_company.py failed.")
        tail = (result.stderr or result.stdout or "").strip()[-1500:]
        if tail:
            st.code(tail)
        return None

    key = company_key_for_ticker(ticker)
    if not key:
        m = re.search(r"company='([^']+)'", result.stdout or "")
        key = m.group(1) if m else None
    return key


def parse_computed_blocks(company: str) -> dict[str, list[tuple[str, float]]]:
    """Parse the computed_metrics chunk into {block title: [(date, value)]},
    newest first. Blocks: SBC %, FCF margin, revenue growth, gross margin,
    Rule of 40 — exactly as built by analyse_company.build_computed_chunk."""
    doc = db[MONGO_COMPANY_COLLECTION].find_one(
        {"company": company, "source_type": "computed_metrics"})
    if not doc:
        return {}
    blocks, current = {}, None
    for line in doc.get("text", "").split("\n"):
        if line and not line.startswith(" ") and line.endswith(":"):
            current = line[:-1]
            blocks[current] = []
        elif current:
            m = re.match(r"\s+(\d{4}-\d{2}-\d{2}):\s*(\S+)", line)
            if m:
                val = pct_to_float(m.group(2))
                if val is None:
                    try:
                        val = float(m.group(2))
                    except ValueError:
                        continue
                blocks[current].append((m.group(1), val))
    return blocks


def block_series(blocks: dict, prefix: str) -> list[tuple[str, float]]:
    for title, series in blocks.items():
        if title.startswith(prefix):
            return series
    return []


# ==============================================================================
# SIDEBAR
# ==============================================================================

with st.sidebar:
    if st.session_state["collapsed"]:
        st.markdown('<div style="text-align:center;font-size:1.6rem;">🪑</div>',
                    unsafe_allow_html=True)
        if st.button("»", key="expand", help="Expand sidebar"):
            st.session_state["collapsed"] = False
            st.rerun()
    else:
        st.markdown(
            '<div style="font-size:1.25rem;font-weight:700;">🪑 Kitchen Table</div>'
            f'<div style="color:#94A3B8;font-size:0.8rem;margin-bottom:0.8rem;">Equity Research</div>',
            unsafe_allow_html=True,
        )

        with st.form("sidebar_search", clear_on_submit=True, border=False):
            side_q = st.text_input("Search", placeholder="Search ticker...",
                                   label_visibility="collapsed")
            if st.form_submit_button("Search", use_container_width=True) and side_q:
                go_to_ticker(side_q)
                st.rerun()

        st.markdown('<div style="height:0.6rem;"></div>', unsafe_allow_html=True)

        # Navigation — Insights is live; the rest are placeholders.
        if st.button("◈  Insights", key="nav_insights", use_container_width=True):
            st.session_state["view"] = "home"
            st.rerun()
        st.markdown(f"""
            <div style="background:{BLUE};border-radius:8px;height:3px;
                        margin:-0.5rem 0 0.4rem 0;"></div>
            <div style="color:#64748B;padding:0.45rem 0.7rem;">☆  Watchlists</div>
            <div style="color:#64748B;padding:0.45rem 0.7rem;">📈  Charts</div>
            <div style="color:#64748B;padding:0.45rem 0.7rem;">📅  Earnings</div>
        """, unsafe_allow_html=True)

        if st.session_state["recent"]:
            st.markdown(
                '<div style="color:#94A3B8;font-size:0.72rem;letter-spacing:0.12em;'
                'margin-top:1.2rem;">RECENT</div>', unsafe_allow_html=True)
            for t in st.session_state["recent"]:
                if st.button(f"• {t}", key=f"recent_{t}", use_container_width=True):
                    go_to_ticker(t)
                    st.rerun()

        st.markdown('<div style="height:2rem;"></div>', unsafe_allow_html=True)
        if st.button("«  Collapse", key="collapse"):
            st.session_state["collapsed"] = True
            st.rerun()


# ==============================================================================
# TOP BAR — live indices + date
# ==============================================================================

def render_top_bar() -> None:
    cells = []
    for label, value, chg in fetch_indices():
        colour = GREEN if chg >= 0 else RED
        sign = "+" if chg >= 0 else ""
        cells.append(
            f'<span style="margin-right:1.6rem;">'
            f'<span style="color:{MUTED};font-size:0.8rem;">{label}</span> '
            f'<b style="font-size:0.88rem;">{value:,.0f}</b> '
            f'<span style="color:{colour};font-size:0.8rem;">{sign}{chg:.2f}%</span>'
            f'</span>'
        )
    indices_html = "".join(cells) or f'<span class="kt-muted">Indices unavailable</span>'
    today = datetime.now().strftime("%A, %d %B %Y")
    st.markdown(f"""
        <div style="background:#FFFFFF;border:1px solid {BORDER};border-radius:12px;
                    box-shadow:0 1px 3px rgba(0,0,0,0.04);padding:0.6rem 1.2rem;
                    margin-bottom:1.4rem;display:flex;justify-content:space-between;
                    align-items:center;">
            <div>{indices_html}</div>
            <div style="color:{MUTED};font-size:0.85rem;">{today}</div>
        </div>
    """, unsafe_allow_html=True)


# ==============================================================================
# VIEW 1 — HOME
# ==============================================================================

def render_home() -> None:
    st.markdown('<div style="height:12vh;"></div>', unsafe_allow_html=True)

    st.markdown(f"""
        <div style="text-align:center;">
            <div class="kt-eyebrow">The Kitchen Table</div>
            <div style="font-size:2.6rem;font-weight:800;color:#0F172A;
                        margin:0.4rem 0 0.2rem 0;">Research any company. In minutes.</div>
            <div style="color:{MUTED};font-size:1.05rem;margin-bottom:1.6rem;">
                5 legendary investors working side by side with you.</div>
        </div>
    """, unsafe_allow_html=True)

    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        with st.form("hero_search", clear_on_submit=True, border=False):
            c1, c2 = st.columns([5, 1])
            with c1:
                q = st.text_input("Ticker", label_visibility="collapsed",
                                  placeholder="Enter a ticker — MDB, DDOG, NVDA, SHOP...")
            with c2:
                submitted = st.form_submit_button("Research", use_container_width=True)
            if submitted and q:
                go_to_ticker(q)
                st.rerun()

        if st.session_state["recent"]:
            chip_cols = st.columns(len(st.session_state["recent"]))
            for col, t in zip(chip_cols, st.session_state["recent"]):
                with col:
                    if st.button(t, key=f"home_recent_{t}", use_container_width=True):
                        go_to_ticker(t)
                        st.rerun()

    # Agent preview row
    pills = "".join(
        f'<span style="display:inline-flex;align-items:center;background:#FFFFFF;'
        f'border:1px solid {BORDER};border-radius:999px;padding:0.3rem 0.9rem 0.3rem 0.35rem;'
        f'margin:0.25rem;box-shadow:0 1px 3px rgba(0,0,0,0.04);">'
        f'<span class="kt-avatar" style="background:{hex_colour(a)};width:26px;height:26px;'
        f'font-size:0.68rem;border-radius:999px;">{initials(AGENT_DISPLAY[a])}</span>'
        f'<span style="font-size:0.85rem;color:#0F172A;">{esc(AGENT_DISPLAY[a])}</span></span>'
        for a in ACTIVE_AGENTS
    )
    st.markdown(f'<div style="text-align:center;margin-top:2.2rem;">{pills}</div>',
                unsafe_allow_html=True)


# ==============================================================================
# VIEW 2 — COMPANY PAGE
# ==============================================================================

def render_company_header(ticker: str, company: str, info: dict) -> None:
    name     = info.get("longName") or info.get("shortName") or company
    exchange = info.get("fullExchangeName") or info.get("exchange") or ""
    sector   = info.get("sector") or ""
    price    = info.get("regularMarketPrice") or info.get("currentPrice")
    prev     = info.get("regularMarketPreviousClose") or info.get("previousClose")
    post     = info.get("postMarketPrice")
    earnings = fetch_next_earnings(ticker)

    chg_html = ""
    if price and prev:
        chg = (price - prev) / prev * 100
        colour = GREEN if chg >= 0 else RED
        arrow = "▲" if chg >= 0 else "▼"
        chg_html = (f'<span style="color:{colour};font-weight:600;">'
                    f'{arrow} {price - prev:+,.2f} ({chg:+.2f}%)</span>')
    post_html = (f'<div class="kt-muted">After hours: ${post:,.2f}</div>'
                 if post else "")
    earn_html = (f'<div class="kt-muted">Next earnings: {esc(earnings)}</div>'
                 if earnings else "")
    badges = "".join(f'<span class="kt-badge">{esc(b)}</span>'
                     for b in [ticker, exchange, sector] if b)

    st.markdown(f"""
        <div class="kt-card" style="display:flex;justify-content:space-between;align-items:center;">
            <div style="display:flex;align-items:center;">
                <span class="kt-avatar" style="background:{BLUE};width:52px;height:52px;
                      font-size:1.05rem;">{initials(name)}</span>
                <div>
                    <div style="font-size:1.35rem;font-weight:700;color:#0F172A;">{esc(name)}</div>
                    <div style="margin-top:0.25rem;">{badges}</div>
                </div>
            </div>
            <div style="text-align:right;">
                <div style="font-size:1.7rem;font-weight:800;color:#0F172A;">
                    {f"${price:,.2f}" if price else "—"}</div>
                {chg_html}{post_html}{earn_html}
            </div>
        </div>
    """, unsafe_allow_html=True)


def render_metric_cards(company: str) -> None:
    blocks   = parse_computed_blocks(company)
    snapshot = engine._financial_snapshot_data(company) or {}
    quarters = snapshot.get("quarters", [])

    # Revenue TTM — sum of the last 4 quarterly revenues from the ingested chunks.
    revs = [engine._money_to_float(q["revenue"]) for q in quarters]
    revs = [r for r in revs if r is not None]
    rev_ttm = fmt_money(sum(revs)) if len(revs) == 4 else (fmt_money(sum(revs)) + "*" if revs else "N/A")

    growth = block_series(blocks, "Revenue Growth YoY")
    gm     = block_series(blocks, "Gross Margin Trend")
    fcf    = block_series(blocks, "FCF Margin")
    sbc    = block_series(blocks, "SBC as % of Revenue")

    def latest(series):  return series[0][1] if series else None
    def previous(series): return series[1][1] if len(series) > 1 else None

    cards = [
        ("Revenue (TTM)", rev_ttm,
         f"{latest(growth):+.1f}% YoY" if latest(growth) is not None else "",
         trend_arrow(latest(growth), previous(growth))),
        ("Gross Margin",
         f"{latest(gm):.1f}%" if latest(gm) is not None else "N/A", "",
         trend_arrow(latest(gm), previous(gm))),
        ("FCF Margin",
         f"{latest(fcf):.1f}%" if latest(fcf) is not None else "N/A", "",
         trend_arrow(latest(fcf), previous(fcf))),
        ("SBC % of Revenue",
         f"{latest(sbc):.1f}%" if latest(sbc) is not None else "N/A", "",
         trend_arrow(latest(sbc), previous(sbc), up_is_good=False)),
    ]

    cols = st.columns(4)
    for col, (label, value, sub, arrow) in zip(cols, cards):
        with col:
            st.markdown(f"""
                <div class="kt-card" style="padding:1rem 1.2rem;">
                    <div class="kt-muted" style="font-size:0.78rem;text-transform:uppercase;
                         letter-spacing:0.08em;">{label}</div>
                    <div style="font-size:1.45rem;font-weight:700;color:#0F172A;">
                        {value} {arrow}</div>
                    <div class="kt-muted">{sub}</div>
                </div>
            """, unsafe_allow_html=True)


def render_price_chart(ticker: str) -> None:
    st.markdown('<div class="kt-card" style="padding-bottom:0.4rem;">'
                '<b>Share price</b></div>', unsafe_allow_html=True)
    btn_cols = st.columns([1, 1, 1, 1, 8])
    for col, period in zip(btn_cols, PERIOD_MAP):
        with col:
            kind = "primary" if st.session_state["chart_period"] == period else "secondary"
            if st.button(period, key=f"period_{period}", type=kind):
                st.session_state["chart_period"] = period
                st.rerun()
    closes = fetch_closes(ticker, PERIOD_MAP[st.session_state["chart_period"]])
    if closes is not None and len(closes):
        st.line_chart(closes, color=BLUE, height=300)
    else:
        st.info("Price history unavailable for this ticker.")


def render_quarterly_table(company: str) -> None:
    snapshot = engine._financial_snapshot_data(company)
    quarters = (snapshot or {}).get("quarters", [])
    if not quarters:
        st.info("No quarterly financials in the database yet for this company.")
        return

    def cell(curr_q, prev_q, key, up_is_good=True):
        raw = curr_q.get(key) or "N/A"
        arrow = trend_arrow(
            pct_to_float(curr_q.get(key)) if "%" in str(curr_q.get(key)) else engine._money_to_float(curr_q.get(key)),
            (pct_to_float(prev_q.get(key)) if "%" in str(prev_q.get(key)) else engine._money_to_float(prev_q.get(key))) if prev_q else None,
            up_is_good,
        )
        return f"{esc(raw)} {arrow}"

    header = ["Period", "Revenue", "Gross Margin", "Op. Margin", "FCF", "SBC"]
    td = 'style="padding:0.45rem 0.8rem;border-bottom:1px solid #F1F5F9;font-size:0.9rem;"'
    rows_html = ""
    for i, q in enumerate(quarters):
        prev_q = quarters[i + 1] if i + 1 < len(quarters) else None
        yoy = f' <span class="kt-muted">({esc(q["yoy"])})</span>' if q.get("yoy") else ""
        rows_html += (
            "<tr>"
            f'<td {td[:-1]}font-weight:600;">{esc(q["label"])}</td>'
            f'<td {td}>{cell(q, prev_q, "revenue")}{yoy}</td>'
            f'<td {td}>{cell(q, prev_q, "gross_margin")}</td>'
            f'<td {td}>{cell(q, prev_q, "op_margin")}</td>'
            f'<td {td}>{cell(q, prev_q, "fcf")}</td>'
            f'<td {td}>{cell(q, prev_q, "sbc", up_is_good=False)}</td>'
            "</tr>"
        )
    head_html = "".join(
        f'<th style="text-align:left;color:{MUTED};font-size:0.78rem;'
        f'text-transform:uppercase;letter-spacing:0.06em;padding:0.4rem 0.8rem;">{h}</th>'
        for h in header)
    st.markdown(f"""
        <div class="kt-card">
            <b>Quarterly financials</b>
            <table style="width:100%;border-collapse:collapse;margin-top:0.6rem;">
                <tr style="border-bottom:1px solid {BORDER};">{head_html}</tr>
                {rows_html}
            </table>
        </div>
    """, unsafe_allow_html=True)


def render_kitchen_table_panel(company: str) -> None:
    with st.expander("🪑 Sit at the Kitchen Table", expanded=False):
        st.markdown(f'<div class="kt-eyebrow" style="margin-bottom:0.5rem;">'
                    f'Select your investors</div>', unsafe_allow_html=True)

        cols = st.columns(len(ACTIVE_AGENTS))
        selected = []
        for col, agent in zip(cols, ACTIVE_AGENTS):
            with col:
                st.markdown(
                    f'<div style="text-align:center;margin-bottom:0.3rem;">'
                    f'<span class="kt-avatar" style="background:{hex_colour(agent)};">'
                    f'{initials(AGENT_DISPLAY[agent])}</span></div>',
                    unsafe_allow_html=True)
                if st.checkbox(AGENT_DISPLAY[agent], key=f"agent_{agent}"):
                    selected.append(agent)

        st.markdown(f'<div class="kt-eyebrow" style="margin:1rem 0 0.5rem 0;">'
                    f'Debate topic</div>', unsafe_allow_html=True)
        topic = st.text_area(
            "Topic", label_visibility="collapsed", height=90, key="topic_input",
            placeholder=("e.g. Does MongoDB's Atlas model create durable competitive "
                         "advantages against hyperscaler competition?"))

        left, right = st.columns([2, 1])
        with left:
            turns = st.number_input("Turns per agent", min_value=1, max_value=5,
                                    value=2, key="turns_input")
        with right:
            st.markdown('<div style="height:1.75rem;"></div>', unsafe_allow_html=True)
            run_clicked = st.button("▶  Run the Debate", type="primary",
                                    use_container_width=True)

        if run_clicked:
            if not selected:
                st.warning("Select at least one investor.")
            elif not topic.strip():
                st.warning("Enter a debate topic.")
            else:
                st.session_state.update({
                    "agents":         selected,
                    "topic":          topic.strip(),
                    "turns":          int(turns),
                    "company":        company,
                    "debate_history": [],
                    "pdf_path":       None,
                    "debate_done":    False,
                    "view":           "debate",
                })
                st.rerun()


def render_company() -> None:
    ticker = st.session_state["ticker"]
    if not ticker:
        st.session_state["view"] = "home"
        st.rerun()

    company = st.session_state["company"] or ensure_company_data(ticker)
    if not company:
        return
    st.session_state["company"] = company

    info = fetch_info(ticker)
    render_company_header(ticker, company, info)
    render_metric_cards(company)
    render_price_chart(ticker)
    render_quarterly_table(company)
    render_kitchen_table_panel(company)


# ==============================================================================
# VIEW 3 — DEBATE OUTPUT
# ==============================================================================

def parse_response(text: str) -> tuple[str, list[str], list[str], list[str]]:
    """Split an agent turn into (conviction, bullets, go-verify items, other lines)."""
    conviction, bullets, go_verify, others = "", [], [], []
    in_gv = False
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line == "---":
            continue
        low = line.lower().lstrip("*")
        if low.startswith("go verify"):
            in_gv = True
            rest = re.sub(r"^[\s:\-–—]+", "", line.lstrip("*")[len("go verify"):].strip("*"))
            if rest.strip():
                go_verify.append(rest.strip())
            continue
        if line.startswith(("- ", "* ", "• ")):
            (go_verify if in_gv else bullets).append(line[2:].strip().replace("**", ""))
        elif not conviction:
            conviction = line.replace("**", "").removeprefix("Conviction:").strip()
        else:
            others.append(line.replace("**", ""))
    return conviction, bullets, go_verify, others


def turn_card_html(entry: dict) -> str:
    agent = entry["agent"]
    conviction, bullets, go_verify, others = parse_response(entry["response"])
    colour = hex_colour(agent)

    bullets_html = "".join(
        f'<li style="margin-bottom:0.3rem;font-size:0.92rem;">{esc(b)}</li>'
        for b in bullets)
    others_html = "".join(
        f'<div style="font-size:0.92rem;margin:0.3rem 0;">{esc(o)}</div>'
        for o in others)
    gv_html = ""
    if go_verify:
        items = "".join(f"<div>• {esc(g)}</div>" for g in go_verify)
        gv_html = (f'<div style="font-style:italic;color:{MUTED};font-size:0.85rem;'
                   f'margin-top:0.6rem;"><b>Go verify:</b>{items}</div>')

    return f"""
        <div class="kt-card">
            <div style="display:flex;align-items:center;margin-bottom:0.6rem;">
                <span style="width:10px;height:10px;border-radius:999px;
                      background:{colour};margin-right:0.55rem;"></span>
                <b style="color:#0F172A;">{esc(display_name(agent))}</b>
                <span class="kt-muted" style="margin-left:0.6rem;">Turn {entry["turn"]}</span>
            </div>
            <div style="border-left:3px solid {BLUE};background:{BLUE_LIGHT};
                  border-radius:0 8px 8px 0;padding:0.55rem 0.9rem;font-weight:600;
                  font-size:0.95rem;margin-bottom:0.6rem;">{esc(conviction)}</div>
            <ul style="margin:0 0 0 1.1rem;padding:0;">{bullets_html}</ul>
            {others_html}{gv_html}
        </div>
    """


def synthesis_card_html(entry: dict) -> str:
    paragraphs = "".join(
        f'<p style="font-size:0.92rem;margin:0.5rem 0;">{esc(p.strip())}</p>'
        for p in entry["response"].split("\n\n") if p.strip())
    return f"""
        <div class="kt-card" style="background:{BLUE_LIGHT};border-color:{BLUE};">
            <div style="display:flex;align-items:center;margin-bottom:0.4rem;">
                <span style="width:10px;height:10px;border-radius:999px;
                      background:{BLUE};margin-right:0.55rem;"></span>
                <b style="color:#0F172A;">Analyst Synthesis</b>
            </div>
            {paragraphs}
        </div>
    """


def render_history(history: list[dict], placeholder) -> None:
    cards = [
        synthesis_card_html(h) if h["agent"] == "synthesis" else turn_card_html(h)
        for h in history
    ]
    placeholder.markdown("".join(cards), unsafe_allow_html=True)


def stream_round_live(topic, company, agents, turns, placeholder) -> list[dict]:
    """Run one debate round, updating the display after every agent turn.
    Uses the engine's own graph via .stream() — same orchestration as
    run_round(), but yields state after each node. Falls back to the plain
    blocking run_round() if streaming isn't available."""
    try:
        intent = engine.classify_topic_intent(topic)
        graph = engine.build_debate_graph()
        state = {
            "topic": topic, "company": company, "intent": intent,
            "agents": agents, "turns": turns, "history": [],
            "turn": 1, "agent_index": 0, "round": 1,
            "finished": False, "audit": False,
        }
        history = []
        limit = turns * len(agents) + 10
        for snapshot in graph.stream(state, config={"recursion_limit": limit},
                                     stream_mode="values"):
            history = snapshot.get("history", history)
            render_history(history, placeholder)
        return history
    except Exception:
        history = run_round(topic, company, agents, turns, 1, [], audit=False)
        render_history(history, placeholder)
        return history


def render_debate() -> None:
    topic   = st.session_state["topic"]
    company = st.session_state["company"]
    agents  = st.session_state["agents"]
    turns   = st.session_state["turns"]

    if not (topic and company and agents):
        st.session_state["view"] = "home"
        st.rerun()

    # Header row
    left, right = st.columns([3, 1])
    with left:
        st.markdown(f"""
            <div style="font-size:1.5rem;font-weight:800;color:#0F172A;">
                {esc(company)} <span style="color:{MUTED};font-weight:500;">· Debate Output</span></div>
            <div class="kt-muted">{esc(" · ".join(display_name(a) for a in agents))}
                &nbsp;|&nbsp; {turns} turn{"s" if turns != 1 else ""} per agent
                &nbsp;|&nbsp; {datetime.now().strftime("%d %B %Y")}</div>
            <div class="kt-muted" style="margin-bottom:1rem;">“{esc(topic)}”</div>
        """, unsafe_allow_html=True)
    with right:
        if st.session_state["pdf_path"] and os.path.exists(st.session_state["pdf_path"]):
            with open(st.session_state["pdf_path"], "rb") as f:
                st.download_button("⬇ Download PDF", f,
                                   file_name=os.path.basename(st.session_state["pdf_path"]),
                                   mime="application/pdf", use_container_width=True)
        if st.button(f"← Back to {st.session_state['ticker']}", use_container_width=True):
            st.session_state["view"] = "company"
            st.rerun()

    placeholder = st.empty()

    if not st.session_state["debate_done"]:
        with st.spinner("The investors are taking their seats..."):
            history = stream_round_live(topic, company, agents, turns, placeholder)
        st.session_state["debate_history"] = history

        # Save the PDF via the engine's own writer
        title = engine.generate_round_title(topic, engine.anthropic_client)
        all_rounds = [{"round": 1, "topic": topic, "title": title, "history": history}]
        try:
            st.session_state["pdf_path"] = save_pdf(all_rounds, agents, company, turns)
        except SystemExit:
            st.error("PDF generation failed — is reportlab installed?")
        st.session_state["debate_done"] = True
        st.rerun()

    render_history(st.session_state["debate_history"], placeholder)

    if st.session_state["pdf_path"] and os.path.exists(st.session_state["pdf_path"]):
        with open(st.session_state["pdf_path"], "rb") as f:
            st.download_button("⬇ Download Full PDF Report", f,
                               file_name=os.path.basename(st.session_state["pdf_path"]),
                               mime="application/pdf", key="pdf_bottom")


# ==============================================================================
# ROUTER
# ==============================================================================

render_top_bar()

if st.session_state["view"] == "company":
    render_company()
elif st.session_state["view"] == "debate":
    render_debate()
else:
    render_home()
