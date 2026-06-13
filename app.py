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

# ── Inject Streamlit secrets → os.environ (Streamlit Community Cloud) ──
# On the cloud, secrets live in st.secrets (set in the Cloud dashboard).
# Locally, they come from .env via config.py's load_dotenv() — no secrets.toml needed.
for _key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "MONGODB_URI"):
    try:
        if _key in st.secrets:
            os.environ.setdefault(_key, st.secrets[_key])
    except Exception:
        pass  # local dev without a secrets.toml — fall through to .env

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
    "view":           "home",     # "home" | "company"
    "ticker":         "",
    "company":        "",
    "agents":         [],
    "topic":          "",
    "turns":          2,
    "debate_history": [],
    "pdf_path":       None,
    "recent":         [],
    "search_results": [],         # list of {"symbol": str, "name": str, "exchange": str}
    "chart_period":   "6M",
    "collapsed":      False,
    "debate_done":    False,
    "debate_active":  False,      # True once a debate is launched on the company page
    "all_rounds":     [],         # list of round dicts {round, topic, title, history} for PDF
    "round_num":      1,          # current debate round number
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
    st.session_state["search_results"] = []
    # New company — clear any debate from the previous one.
    st.session_state.update({
        "debate_active":  False,
        "debate_done":    False,
        "debate_history": [],
        "pdf_path":       None,
        "all_rounds":     [],
        "round_num":      1,
    })


# ==============================================================================
# GLOBAL CSS
# ==============================================================================

SIDEBAR_WIDTH = "220px"

st.markdown(f"""
<style>
/* ── Canvas ── */
html, body, [class*="css"] {{
    font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
}}
.stApp {{ background: #F8FAFC; }}
.block-container {{ padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1200px; }}
header[data-testid="stHeader"] {{ background: transparent; }}

/* ── Hide Streamlit's sidebar collapse toggle ── */
[data-testid="stSidebarCollapseButton"] {{ display: none !important; }}
button[aria-label="Close sidebar"] {{ display: none !important; }}

/* ── Sidebar: dark navy, fixed width, always visible ── */
[data-testid="stSidebar"] {{
    background: {NAVY};
    min-width: {SIDEBAR_WIDTH}; max-width: {SIDEBAR_WIDTH};
    border-right: 1px solid #1E293B;
    transform: translateX(0) !important;
    visibility: visible !important;
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
    color: #1E293B !important;
}}
input, textarea {{
    color: #1E293B !important;
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

/* ── Hero search button — prevent text wrap ── */
[data-testid="stBaseButton-secondaryFormSubmit"] {{
    white-space: nowrap !important;
}}

/* ── Main area text fix (white-on-white bug) ── */
section[data-testid="stMain"] {{
    color: #1E293B;
}}
section[data-testid="stMain"] p,
section[data-testid="stMain"] label,
section[data-testid="stMain"] .stMarkdown,
section[data-testid="stMain"] .stTextInput label,
section[data-testid="stMain"] .stNumberInput label,
section[data-testid="stMain"] .stTextArea label,
section[data-testid="stMain"] .stCheckbox label,
section[data-testid="stMain"] .stSelectbox label {{
    color: #1E293B !important;
}}

/* ── Period selector buttons (1M/3M/6M/1Y) — dark with white text ── */
section[data-testid="stMain"] [data-testid="stBaseButton-secondary"] {{
    background: #1E293B !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 8px !important;
    font-size: 0.8rem !important;
}}
section[data-testid="stMain"] [data-testid="stBaseButton-secondary"] p,
section[data-testid="stMain"] [data-testid="stBaseButton-secondary"] span {{
    color: #FFFFFF !important;
}}
section[data-testid="stMain"] [data-testid="stBaseButton-secondary"]:hover {{
    background: #334155 !important;
    color: #FFFFFF !important;
}}

/* ── Market ticker scroll animation ── */
@keyframes kt-scroll {{
    0%   {{ transform: translateX(0); }}
    100% {{ transform: translateX(-50%); }}
}}
.kt-ticker {{
    display: inline-flex;
    align-items: center;
    animation: kt-scroll 28s linear infinite;
    white-space: nowrap;
}}
.kt-ticker:hover {{
    animation-play-state: paused;
}}
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
    """[(label, last close, % change)] for major global indices."""
    INDICES = [
        ("^DJI",   "Dow"),
        ("^GSPC",  "S&P 500"),
        ("^IXIC",  "Nasdaq"),
        ("^RUT",   "Russell"),
        ("^N225",  "Nikkei"),
        ("^STI",   "SGX"),
        ("^FTSE",  "FTSE"),
        ("^KS11",  "KOSPI"),
    ]
    out = []
    syms = " ".join(s for s, _ in INDICES)
    try:
        data = yf.download(syms, period="2d", progress=False)
        closes = data["Close"]
        for sym, label in INDICES:
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


@st.cache_data(ttl=60, show_spinner=False)
def search_tickers(query: str) -> list[dict]:
    """Return up to 6 ticker matches for a company name or partial ticker."""
    try:
        results = yf.Search(query, max_results=6).quotes
        out = []
        for r in results:
            symbol = r.get("symbol", "")
            name = r.get("shortname") or r.get("longname") or ""
            exchange = r.get("exchDisp") or r.get("exchange") or ""
            if symbol and name:
                out.append({"symbol": symbol, "name": name, "exchange": exchange})
        return out
    except Exception:
        return []


def handle_search_submit(query: str) -> None:
    """Route a search submission: exact-looking ticker → straight to the company
    view; otherwise fuzzy-match company names and show a results list to pick."""
    query = query.strip()
    if not query:
        return
    # Looks like a ticker already (1-5 letters) — go direct.
    if re.match(r'^[A-Za-z]{1,5}$', query):
        go_to_ticker(query)
        st.rerun()
    else:
        results = search_tickers(query)
        if len(results) == 1:
            go_to_ticker(results[0]["symbol"])
            st.rerun()
        elif results:
            st.session_state["search_results"] = results
            st.rerun()
        else:
            st.warning(f"No results found for '{query}'")


def render_search_results(key_prefix: str = "pick") -> None:
    """Render the fuzzy-search results list with a Select button per row.
    `key_prefix` keeps button keys unique when shown in more than one place."""
    if not st.session_state["search_results"]:
        return
    st.markdown('<div style="max-width:480px;margin:0.6rem auto 0 auto;">',
                unsafe_allow_html=True)
    for r in st.session_state["search_results"]:
        col1, col2 = st.columns([4, 1])
        with col1:
            st.markdown(
                f'<div style="padding:0.4rem 0;font-size:0.92rem;">'
                f'<b style="color:#0F172A;">{esc(r["symbol"])}</b> '
                f'<span style="color:#64748B;">{esc(r["name"])}</span> '
                f'<span style="color:#94A3B8;font-size:0.78rem;">{esc(r["exchange"])}</span>'
                f'</div>',
                unsafe_allow_html=True
            )
        with col2:
            if st.button("Select", key=f"{key_prefix}_{r['symbol']}",
                         use_container_width=True):
                st.session_state["search_results"] = []
                go_to_ticker(r["symbol"])
                st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)


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
    st.markdown(
        '<div style="font-size:1.25rem;font-weight:700;">🪑 Kitchen Table</div>'
        f'<div style="color:#94A3B8;font-size:0.8rem;margin-bottom:0.8rem;">Equity Research</div>',
        unsafe_allow_html=True,
    )

    with st.form("sidebar_search", clear_on_submit=True, border=False):
        side_q = st.text_input("Search", placeholder="Search ticker...",
                               label_visibility="collapsed")
        if st.form_submit_button("Search", use_container_width=True) and side_q:
            handle_search_submit(side_q)

    st.markdown('<div style="height:0.6rem;"></div>', unsafe_allow_html=True)

    # Navigation — Insights is live; the rest are placeholders.
    if st.button("◈  Insights", key="nav_insights", use_container_width=True):
        st.session_state["view"] = "home"
        st.rerun()
    def _soon_item(icon: str, label: str) -> str:
        return (
            '<div style="display:flex;align-items:center;justify-content:space-between;'
            'padding:0.45rem 0.7rem;color:#94A3B8;cursor:default;user-select:none;">'
            f'<span style="color:#94A3B8;">{icon}&nbsp;&nbsp;{label}</span>'
            '<span style="background:#1E293B;color:#94A3B8;font-size:0.6rem;'
            'font-weight:700;letter-spacing:0.06em;padding:0.1rem 0.45rem;'
            'border-radius:6px;border:1px solid #334155;">SOON</span>'
            '</div>')

    st.markdown(f"""
        <div style="background:{BLUE};border-radius:8px;height:3px;
                    margin:-0.5rem 0 0.4rem 0;"></div>
        {_soon_item("☆", "Watchlists")}
        {_soon_item("📈", "Charts")}
        {_soon_item("📅", "Earnings")}
    """, unsafe_allow_html=True)

    if st.session_state["recent"]:
        st.markdown(
            '<div style="color:#94A3B8;font-size:0.72rem;letter-spacing:0.12em;'
            'margin-top:1.2rem;">RECENT</div>', unsafe_allow_html=True)
        for t in st.session_state["recent"]:
            if st.button(f"• {t}", key=f"recent_{t}", use_container_width=True):
                go_to_ticker(t)
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
            f'<span style="margin-right:1.6rem;white-space:nowrap;">'
            f'<span style="color:{MUTED};font-size:0.74rem;font-weight:500;">{label}</span>'
            f'<span style="color:{colour};font-size:0.74rem;margin-left:0.3rem;">{sign}{chg:.2f}%</span>'
            f'</span>'
        )
    items_html = "".join(cells)
    # Duplicate items for seamless loop
    ticker_html = items_html + items_html if items_html else f'<span style="color:{MUTED};font-size:0.74rem;">Indices unavailable</span>'
    today = datetime.now().strftime("%a, %d %b %Y")
    st.markdown(f"""
        <div style="background:#FFFFFF;border:1px solid {BORDER};border-radius:12px;
                    box-shadow:0 1px 3px rgba(0,0,0,0.04);padding:0.5rem 1.2rem;
                    margin-bottom:1.4rem;display:flex;align-items:center;">
            <div style="overflow:hidden;flex:1;min-width:0;">
                <div class="kt-ticker">{ticker_html}</div>
            </div>
            <div style="color:#0F172A;font-size:0.84rem;font-weight:700;white-space:nowrap;margin-left:1rem;flex-shrink:0;">{today}</div>
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
                Legendary investors working side by side with you.</div>
        </div>
    """, unsafe_allow_html=True)

    _, mid, _ = st.columns([0.2, 3.6, 0.2])
    with mid:
        with st.form("hero_search", clear_on_submit=True, border=False):
            c1, c2 = st.columns([5, 1])
            with c1:
                q = st.text_input("Ticker", label_visibility="collapsed",
                                  placeholder="Company name or ticker — MongoDB, MDB, Nvidia...")
            with c2:
                submitted = st.form_submit_button("Research", use_container_width=True)
            if submitted and q:
                handle_search_submit(q)

        render_search_results("pick")

        if st.session_state["recent"]:
            chip_cols = st.columns(len(st.session_state["recent"]))
            for col, t in zip(chip_cols, st.session_state["recent"]):
                with col:
                    if st.button(t, key=f"home_recent_{t}", use_container_width=True):
                        go_to_ticker(t)
                        st.rerun()



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
    st.markdown('<div class="kt-card" style="padding-bottom:0.4rem;text-align:center;">'
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
                    "debate_active":  True,   # render inline below, same page
                    "all_rounds":     [],     # fresh debate — reset rounds
                    "round_num":      1,
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
    render_debate_inline(company)


# ==============================================================================
# DEBATE OUTPUT — rendered inline on the company page (below the panel)
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


def _inline_md(s: str) -> str:
    """Convert inline markdown in ALREADY-ESCAPED text: **bold** -> <b>bold</b>.
    (esc() leaves '*' untouched, so the markers survive to here.)"""
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)


def md_to_html(text: str) -> str:
    """Render a Claude markdown response as clean card HTML:
      '## Header'  -> bold section title (no '#' shown)
      '- item'     -> bulleted line (no raw '-'/'*' shown)
      '**bold**'   -> <b>bold</b>
      blank line   -> paragraph break
    Everything is HTML-escaped first, so no raw markup leaks through."""
    parts, para = [], []

    def flush_para():
        if para:
            parts.append(
                f'<p style="font-size:0.92rem;margin:0.5rem 0;">{" ".join(para)}</p>')
            para.clear()

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            flush_para()
            continue
        header = re.match(r"^#{1,6}\s+(.*)$", line)
        bullet = re.match(r"^[-*]\s+(.*)$", line)
        if header:
            flush_para()
            parts.append(
                '<div style="font-weight:700;color:#0F172A;font-size:0.98rem;'
                f'margin:0.7rem 0 0.25rem 0;">{_inline_md(esc(header.group(1)))}</div>')
        elif bullet:
            flush_para()
            parts.append(
                '<div style="font-size:0.92rem;margin:0.15rem 0 0.15rem 0.4rem;">'
                f'• {_inline_md(esc(bullet.group(1)))}</div>')
        else:
            para.append(_inline_md(esc(line)))
    flush_para()
    return "".join(parts)


def synthesis_card_html(entry: dict) -> str:
    body = md_to_html(entry["response"])
    return f"""
        <div class="kt-card" style="background:{BLUE_LIGHT};border-color:{BLUE};">
            <div style="display:flex;align-items:center;margin-bottom:0.4rem;">
                <span style="width:10px;height:10px;border-radius:999px;
                      background:{BLUE};margin-right:0.55rem;"></span>
                <b style="color:#0F172A;">Analyst Synthesis</b>
            </div>
            {body}
        </div>
    """


def render_history(history: list[dict], placeholder) -> None:
    # Render each card as its OWN st.markdown so every card is a clean,
    # self-contained HTML block. Concatenating them into a single markdown call
    # makes Streamlit's parser treat only the first card as HTML and escape the
    # rest to raw text (the synthesis card was dumping its <div> tags on screen).
    # placeholder.container() keeps the st.empty holding a single child, so each
    # streaming update still replaces the previous render cleanly.
    multi_round = len({h.get("round", 1) for h in history}) > 1
    last_round = None
    with placeholder.container():
        for h in history:
            rnd = h.get("round", 1)
            if multi_round and rnd != last_round:
                rnd_topic = esc(h.get("topic", "")) if h.get("topic") else ""
                topic_html = (f'<span class="kt-muted" style="margin-left:0.7rem;'
                              f'font-size:0.82rem;">“{rnd_topic}”</span>'
                              if rnd_topic else
                              '<span style="flex:1;"></span>')
                st.markdown(
                    f'<div style="margin:1.2rem 0 0.4rem 0;display:flex;align-items:center;">'
                    f'<span style="background:{NAVY};color:#FFFFFF;font-size:0.72rem;'
                    f'font-weight:700;letter-spacing:0.08em;padding:0.2rem 0.7rem;'
                    f'border-radius:999px;white-space:nowrap;">ROUND {rnd}</span>'
                    f'{topic_html}'
                    f'</div>', unsafe_allow_html=True)
                last_round = rnd
            card = (synthesis_card_html(h) if h["agent"] == "synthesis"
                    else turn_card_html(h))
            st.markdown(card, unsafe_allow_html=True)


def stream_round_live(topic, company, agents, turns, placeholder,
                      session_history=None, round_num=1) -> list[dict]:
    """Run one debate round, updating the display after every agent turn.
    Uses the engine's own graph via .stream() — same orchestration as
    run_round(), but yields state after each node. Falls back to the plain
    blocking run_round() if streaming isn't available.

    `session_history` carries forward the full transcript of prior rounds so
    the agents (and the graph's per-round synthesis) can reference earlier
    rounds. Returns the FULL accumulated history (prior rounds + this round)."""
    session_history = list(session_history or [])
    try:
        intent = engine.classify_topic_intent(topic)
        graph = engine.build_debate_graph()
        state = {
            "topic": topic, "company": company, "intent": intent,
            "agents": agents, "turns": turns,
            "history": session_history,   # carry full prior history in (not [])
            "turn": 1, "agent_index": 0, "round": round_num,
            "finished": False, "audit": False,
        }
        history = session_history
        limit = turns * len(agents) + 10
        for snapshot in graph.stream(state, config={"recursion_limit": limit},
                                     stream_mode="values"):
            history = snapshot.get("history", history)
            render_history(history, placeholder)
        return history
    except Exception:
        history = run_round(topic, company, agents, turns, round_num,
                            session_history, audit=False)
        render_history(history, placeholder)
        return history


def render_debate_inline(company: str) -> None:
    """Render the debate output directly on the company page, below the Kitchen
    Table panel. Streams live, then shows the PDF download — no separate view."""
    if not st.session_state.get("debate_active"):
        return

    topic  = st.session_state["topic"]
    agents = st.session_state["agents"]
    turns  = st.session_state["turns"]
    if not (topic and agents):
        return

    # Section header + a way to dismiss the output.
    rounds_done = len(st.session_state["all_rounds"])
    header_topic = (st.session_state["all_rounds"][0]["topic"]
                    if rounds_done else topic)
    round_note = (f' &nbsp;|&nbsp; {rounds_done} round{"s" if rounds_done != 1 else ""}'
                  if rounds_done > 1 else "")
    left, right = st.columns([3, 1])
    with left:
        st.markdown(f"""
            <div style="font-size:1.4rem;font-weight:800;color:#0F172A;margin-top:0.6rem;">
                Debate Output</div>
            <div class="kt-muted">{esc(" · ".join(display_name(a) for a in agents))}
                &nbsp;|&nbsp; {turns} turn{"s" if turns != 1 else ""} per agent
                &nbsp;|&nbsp; {datetime.now().strftime("%d %B %Y")}{round_note}</div>
            <div class="kt-muted" style="margin-bottom:1rem;">“{esc(header_topic)}”</div>
        """, unsafe_allow_html=True)
    with right:
        if st.session_state["debate_done"]:
            st.markdown('<div style="height:1.2rem;"></div>', unsafe_allow_html=True)
            if st.button("✕  Clear debate", use_container_width=True):
                st.session_state.update({
                    "debate_active": False, "debate_done": False,
                    "debate_history": [], "pdf_path": None,
                    "all_rounds": [], "round_num": 1,
                })
                st.rerun()

    placeholder = st.empty()
    round_num = st.session_state["round_num"]

    if not st.session_state["debate_done"]:
        with st.spinner("The investors are taking their seats..."):
            # Carry the full prior-rounds transcript in as session_history so
            # this round's agents can reference earlier rounds.
            history = stream_round_live(
                topic, company, agents, turns, placeholder,
                session_history=st.session_state["debate_history"],
                round_num=round_num,
            )
        st.session_state["debate_history"] = history

        # Slice out just THIS round's entries and record it for the PDF, then
        # regenerate the PDF with every round included.
        round_history = [h for h in history if h.get("round") == round_num]
        title = engine.generate_round_title(topic, engine.anthropic_client)
        st.session_state["all_rounds"].append({
            "round": round_num, "topic": topic, "title": title,
            "history": round_history,
        })
        try:
            st.session_state["pdf_path"] = save_pdf(
                st.session_state["all_rounds"], agents, company, turns)
        except SystemExit:
            st.error("PDF generation failed — is reportlab installed?")
        st.session_state["debate_done"] = True
        # Rerun so the transcript renders exactly once on a clean pass. The
        # live writes into `placeholder` during streaming are discarded by the
        # rerun, avoiding the double-render that dumped raw HTML at the end.
        st.rerun()

    # Clean pass (debate_done is True): render the full transcript once.
    render_history(st.session_state["debate_history"], placeholder)

    # ── Follow-up round: only after the current round is done ──
    st.markdown('<div style="height:0.6rem;"></div>', unsafe_allow_html=True)
    with st.container():
        st.markdown(f'<div class="kt-eyebrow" style="margin-bottom:0.4rem;">'
                    f'Continue the conversation</div>', unsafe_allow_html=True)
        follow_up = st.text_area(
            "Follow-up", label_visibility="collapsed", height=80,
            key=f"followup_{round_num}",
            placeholder="Ask a follow-up question...")
        if st.button("▶  Continue the Debate", type="primary",
                     key=f"continue_{round_num}"):
            if follow_up.strip():
                # Keep debate_history and all_rounds intact; just start a new
                # round that builds on everything so far.
                st.session_state["round_num"] = round_num + 1
                st.session_state["topic"] = follow_up.strip()
                st.session_state["debate_done"] = False
                st.rerun()
            else:
                st.warning("Enter a follow-up question.")

    if st.session_state["pdf_path"] and os.path.exists(st.session_state["pdf_path"]):
        with open(st.session_state["pdf_path"], "rb") as f