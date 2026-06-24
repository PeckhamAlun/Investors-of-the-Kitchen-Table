"""
==============================================================================
  MAIN.PY — THE KITCHEN TABLE
  Multi-Agent Equity Debate Engine
  Orchestrates a structured debate between investment agents.
  Outputs a clean PDF transcript readable by anyone.
==============================================================================

  USAGE:
      py -3.11 main.py --topic "Is Datadog a good investment?" --company datadog
      py -3.11 main.py --topic "Is Datadog a good investment?" --agents buffett cathie_wood peter_lynch
      py -3.11 main.py --topic "Is Datadog a good investment?" --agents buffett peter_lynch --turns 3 --first peter_lynch
      py -3.11 main.py   (interactive mode)

  INSTALL:
      py -3.11 -m pip install langgraph reportlab

==============================================================================
"""

import os
import re
import sys
import asyncio
import argparse
import anthropic
from datetime import datetime
from pathlib import Path
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from google import genai as google_genai
from langgraph.graph import StateGraph, END
from typing import TypedDict, List

# ==============================================================================
# CONFIG IMPORT
# ==============================================================================

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    CHROMA_DIR, EMBED_MODEL, N_RESULTS,
    CLAUDE_MODEL, MAX_TOKENS,
    COMPANY_COLLECTION, philosophy_collection,
    system_prompt_path, OUTPUTS_DIR,
    AGENT_DISPLAY, AGENT_COLOURS, normalize_company,
    MONGODB_URI, MONGODB_DB_NAME, GEMINI_EMBED_MODEL,
    GOOGLE_API_KEY, ANTHROPIC_API_KEY,
)

# Latest-ingest lookup lives in the ingestion script. Importing it here lets
# retrieval pin the company filter to the most recent ingest version only —
# historical versions stay in the DB but agents never see stale data.
from scripts.analyse_company import get_latest_ingest_version

# ==============================================================================
# DEBATE SETTINGS (defaults — all overridable via CLI)
# ==============================================================================

DEFAULT_AGENTS      = ["buffett", "cathie_wood"]
DEFAULT_TURNS       = 2
MAX_CONTEXT_CHARS   = 8000
DEBATE_MAX_TOKENS   = 1200


def display_name(agent: str) -> str:
    return AGENT_DISPLAY.get(agent, agent.replace("_", " ").title())


# ==============================================================================
# TOPIC INTENT CLASSIFIER
# ==============================================================================

INTENT_KEYWORDS = {
    "financials": [
        "financial", "financials", "revenue", "earnings", "margin", "margins",
        "profit", "profitability", "cash flow", "fcf", "ebitda", "eps",
        "balance sheet", "debt", "income", "operating", "gross", "net income",
        "sbc", "stock-based compensation", "dilution", "guidance", "beat",
        "miss", "quarter", "annual", "fiscal", "valuation", "multiple", "pe",
        "price to sales", "ev", "enterprise value",
    ],
    "growth": [
        "growth", "expansion", "scale", "tam", "market size", "addressable",
        "opportunity", "trajectory", "runway", "hypergrowth", "compounding",
        "retention", "nrr", "arr", "customer", "land and expand", "upsell",
        "cross-sell", "new product", "product line", "pipeline",
    ],
    "competitive": [
        "competition", "competitive", "competitor", "moat", "advantage",
        "differentiation", "market share", "pricing power", "switching cost",
        "network effect", "hyperscaler", "aws", "azure", "google", "microsoft",
        "incumbent", "disrupt", "threat", "defend", "barrier", "entry",
        "industry", "sector", "landscape", "players",
    ],
    "macro": [
        "macro", "economy", "recession", "rates", "interest rate", "inflation",
        "fed", "cycle", "spending", "budget", "enterprise it", "capex",
        "sentiment", "outlook", "environment", "headwind", "tailwind",
        "ai", "cloud", "infrastructure", "buildout", "adoption",
    ],
    "management": [
        "management", "ceo", "founder", "leadership", "team", "culture",
        "execution", "strategy", "vision", "capital allocation", "buyback",
        "dividend", "acquisition", "m&a", "governance", "insider",
    ],
}

def classify_topic_intent(topic: str) -> str:
    topic_lower = topic.lower()
    scores = {intent: 0 for intent in INTENT_KEYWORDS}
    for intent, keywords in INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in topic_lower:
                scores[intent] += 1
    best_intent = max(scores, key=scores.get)
    return best_intent if scores[best_intent] > 0 else "general"


def build_expansions(query: str, intent: str, company: str | None) -> list[str]:
    """Dynamically generate retrieval expansions using Claude."""
    company_str = company or "the company"
    try:
        response = anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    f"Generate 4 short search queries (5-10 words each) to retrieve relevant "
                    f"investment research context for this debate topic.\n\n"
                    f"Topic: {query}\n"
                    f"Company: {company_str}\n"
                    f"Focus area: {intent}\n\n"
                    f"Rules:\n"
                    f"- Each query must be distinct — cover different angles\n"
                    f"- Queries should surface philosophy/framework content AND company-specific data\n"
                    f"- No preamble, no numbering, one query per line, plain text only"
                )
            }]
        )
        lines = [
            l.strip() for l in response.content[0].text.strip().split("\n")
            if l.strip() and len(l.strip()) > 5
        ]
        return [query] + lines[:4]   # always include the raw query first
    except Exception:
        # Fallback to raw query only if Claude call fails
        return [query, f"investment perspective on {query}", f"{company_str} {intent} analysis"]


def generate_round_title(topic: str, client: anthropic.Anthropic) -> str:
    """Generate a short, clean section title from the topic string."""
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=30,
            messages=[{
                "role": "user",
                "content": (
                    f"Generate a short 3-6 word title that summarises this investment debate topic. "
                    f"No punctuation, no quotes, title case only. Topic: {topic}"
                )
            }]
        )
        return response.content[0].text.strip().strip('"').strip("'")
    except Exception:
        return topic[:50]


# ==============================================================================
# INITIALISE SHARED RESOURCES
# ==============================================================================

print("\n  Initialising Kitchen Table...")
mongo_client     = MongoClient(MONGODB_URI, server_api=ServerApi('1'))
db               = mongo_client[MONGODB_DB_NAME]
gemini_client    = google_genai.Client(api_key=GOOGLE_API_KEY)
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def available_companies() -> list[str]:
    col = db[COMPANY_COLLECTION]
    return sorted(col.distinct("company"))


def load_system_prompt(agent: str) -> str:
    path = system_prompt_path(agent)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"System prompt not found: {path}\n"
            f"Create agents/{agent}/system_prompt.txt first."
        )
    return open(path, encoding="utf-8").read().strip()


def retrieve_records(
    query: str,
    agent: str,
    company: str | None,
    intent: str = "general",
) -> tuple[list[dict], list[str]]:
    expansions = build_expansions(query, intent, company)
    comp_results_per_query = 3 if intent in ("financials", "growth") else 2

    # Pin company retrieval to the LATEST ingest only — historical versions stay
    # in the collection but agents never see stale data. Computed once per call.
    # Falls back to a company-only filter for legacy data that has no
    # ingest_version field (latest_version == 0).
    company_filter = None
    if company:
        _, latest_version = get_latest_ingest_version(company)
        company_filter = {"company": company}
        if latest_version:
            company_filter["ingest_version"] = latest_version

    seen_ids: set = set()
    records: list[dict] = []

    for q in expansions:
        # Embed query using Gemini
        result = gemini_client.models.embed_content(
            model=GEMINI_EMBED_MODEL,
            contents=q
        )
        embedding = list(result.embeddings[0].values)

        # Query philosophy collection
        phil_col = db[philosophy_collection(agent)]
        phil_results = phil_col.aggregate([
            {
                "$vectorSearch": {
                    "index": "vector_index",
                    "path": "embedding",
                    "queryVector": embedding,
                    "numCandidates": 50,
                    "limit": 3,
                    "filter": {"agent": agent}
                }
            },
            {
                "$project": {
                    "text": 1,
                    "source": 1,
                    "agent": 1,
                    "score": {"$meta": "vectorSearchScore"}
                }
            }
        ])

        for doc in phil_results:
            doc_id = str(doc["_id"])
            if doc_id not in seen_ids:
                seen_ids.add(doc_id)
                records.append({
                    "collection": "philosophy",
                    "source": doc.get("source", "philosophy"),
                    "filename": doc.get("source", ""),
                    "doc": doc.get("text", ""),
                })

        # Query company financials
        if company:
            comp_col = db[COMPANY_COLLECTION]
            comp_results = comp_col.aggregate([
                {
                    "$vectorSearch": {
                        "index": "vector_index",
                        "path": "embedding",
                        "queryVector": embedding,
                        "numCandidates": 50,
                        "limit": comp_results_per_query,
                        "filter": company_filter
                    }
                },
                {
                    "$project": {
                        "text": 1,
                        "source": 1,
                        "company": 1,
                        "score": {"$meta": "vectorSearchScore"}
                    }
                }
            ])

            for doc in comp_results:
                doc_id = str(doc["_id"])
                if doc_id not in seen_ids:
                    seen_ids.add(doc_id)
                    records.append({
                        "collection": "company",
                        "source": doc.get("source", f"{company} financials"),
                        "filename": doc.get("source", ""),
                        "doc": doc.get("text", ""),
                    })

    return records, expansions


def _records_to_context(records: list[dict], company: str | None) -> str:
    """Join records into the prompt context block — exact original formatting."""
    chunks = []
    for r in records:
        if r["collection"] == "company":
            chunks.append(f"[{company} financials]\n{r['doc']}")
        else:
            chunks.append(f"[{r['source']}]\n{r['doc']}")
    return "\n\n---\n\n".join(chunks)


def retrieve_context(
    query: str,
    agent: str,
    company: str | None,
    intent: str = "general",
    audit: bool = False,
) -> str:
    records, expansions = retrieve_records(query, agent, company, intent)
    if audit:
        print_retrieval_report(agent, query, company, intent, records, expansions)
    context = _records_to_context(records, company)
    return context[:MAX_CONTEXT_CHARS]


# ==============================================================================
# RETRIEVAL AUDIT REPORT
# Shared by main.py's --audit flag and scripts/audit_rag.py — one code path.
# ==============================================================================

PREVIEW_CHARS = 400
_RULE  = "─" * 56
_HEAVY = "=" * 56


def _print_chunk_section(title: str, records: list[dict]) -> None:
    print(f"\n  {_RULE}")
    print(f"  {title}  ({len(records)} chunk{'s' if len(records) != 1 else ''})")
    print(f"  {_RULE}")
    if not records:
        print("\n  (none retrieved)")
        return
    for i, rec in enumerate(records, 1):
        label = rec["source"]
        if rec["filename"] and rec["filename"] != rec["source"]:
            label = f"{rec['source']}  —  {rec['filename']}"
        preview = rec["doc"][:PREVIEW_CHARS].replace("\n", " ").strip()
        ellipsis = "..." if len(rec["doc"]) > PREVIEW_CHARS else ""
        print(f"\n  [{i}] {label}")
        print(f"      {preview}{ellipsis}")


def _print_hits(title: str, records: list[dict]) -> None:
    """List distinct contributing source files with counts."""
    counts: dict[str, int] = {}
    for rec in records:
        key = rec["filename"] or rec["source"]
        counts[key] = counts.get(key, 0) + 1
    print(f"\n  {title}")
    if not counts:
        print("      (none)")
        return
    for src, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"      • {src}  ({n})")


def print_retrieval_report(
    agent: str,
    query: str,
    company: str | None,
    intent: str,
    records: list[dict],
    expansions: list[str],
) -> None:
    """Full per-agent retrieval audit: chunks by collection, summary, verdict."""
    phil_records = [r for r in records if r["collection"] == "philosophy"]
    comp_records = [r for r in records if r["collection"] == "company"]

    print(f"\n  {_HEAVY}")
    print(f"  RAG AUDIT — {display_name(agent)}")
    print(f"  {_HEAVY}")
    print(f"  Query   : {query}")
    print(f"  Company : {company or 'None'}")
    print(f"  Intent  : {intent.upper()}")
    print(f"  Expansions used:")
    for q in expansions:
        print(f"      - {q}")

    _print_chunk_section("PHILOSOPHY COLLECTION", phil_records)
    _print_chunk_section("COMPANY_FINANCIALS COLLECTION", comp_records)

    total = len(records)
    phil_n, comp_n = len(phil_records), len(comp_records)
    phil_pct = (phil_n / total * 100) if total else 0.0
    comp_pct = (comp_n / total * 100) if total else 0.0

    print(f"\n  {_RULE}")
    print(f"  SUMMARY")
    print(f"  {_RULE}")
    print(
        f"  {total} chunk{'s' if total != 1 else ''} total  |  "
        f"philosophy {phil_n} ({phil_pct:.0f}%)  vs  "
        f"company {comp_n} ({comp_pct:.0f}%)  |  intent: {intent}"
    )

    print(f"\n  {_HEAVY}")
    print(f"  VERDICT")
    print(f"  {_HEAVY}")
    _print_hits("PHILOSOPHY HITS", phil_records)
    _print_hits("COMPANY HITS", comp_records)

    print()
    if company and comp_n == 0:
        print("  WARNING: zero company chunks retrieved — agent is flying blind on financials.")
        print(f"           '{company}' exists in the DB but this query surfaced none of its")
        print("           chunks — the query may be poorly matched to the ingested documents.")
    if total and phil_pct > 80:
        print(f"  WARNING: {phil_pct:.0f}% of chunks are philosophy — agent likely over-relying")
        print("           on training/framework, not company data.")
    if not (company and comp_n == 0) and not (total and phil_pct > 80):
        print("  OK: retrieval balance looks healthy.")
    print()


# ==============================================================================
# LANGGRAPH STATE
# ==============================================================================

class DebateState(TypedDict):
    topic:         str
    company:       str | None
    intent:        str
    agents:        List[str]
    turns:         int
    history:       List[dict]   # {agent, turn, response, round, topic}
    turn:          int
    agent_index:   int
    finished:      bool
    audit:         bool


# ==============================================================================
# AGENT NODE
# ==============================================================================

def agent_node(state: DebateState) -> DebateState:
    agents   = state["agents"]
    agent    = agents[state["agent_index"]]
    topic    = state["topic"]
    company  = state.get("company")
    history  = state["history"]
    turn     = state["turn"]
    turns    = state["turns"]
    intent   = state.get("intent", "general")

    name = display_name(agent)
    print(f"\n  {'─'*56}")
    print(f"  {name.upper()} — Turn {turn}  [{intent}]")
    print(f"  {'─'*56}")

    system_prompt     = load_system_prompt(agent)
    context           = retrieve_context(topic, agent, company, intent=intent, audit=state.get("audit", False))

    # Full session history — agents see everything from all rounds
    history_text = ""
    if history:
        history_text = "\n\n".join([
            f"{display_name(h['agent'])} (Round {h.get('round',1)}, Turn {h['turn']}):\n{h['response']}"
            for h in history[-12:]
        ])

    others      = [a for a in agents if a != agent]
    other_names = " and ".join(display_name(a) for a in others)

    if not history:
        header = f"""Topic for debate: {topic}
        {f'Company under discussion: {company}' if company else ''}

        Relevant context from your research and knowledge base:
        {context if context else '[No specific context retrieved — draw on your frameworks]'}

        YOUR CONTEXT:
        You are presenting as yourself — your own frameworks, your own voice,
        your own convictions — in a structured investment debate with other
        serious investors. Treat this as a high-stakes internal research
        meeting where a portfolio manager is deciding whether this company
        warrants serious capital allocation.

        Your job is not to be balanced. Your job is to give the sharpest,
        most evidenced version of your actual view. If you are bullish, make
        the bull case with precision. If you are skeptical, name exactly what
        would have to be true for you to be wrong.

        Every claim must be grounded in either retrieved financial data or
        your own retrieved investment philosophy — not general knowledge,
        not industry averages, not what you think sounds right. If you do
        not have the data to support a claim, put it in the Go verify section
        instead of stating it as fact.

        The portfolio manager reading this will verify your key points against
        the filings before making any decision. Your value is in the quality
        of the argument and the precision of what you flag as unresolved —
        not in covering every possible angle."""
        footer = """

        If you agree with a point already made, do not restate it. Deepen it or advance to the next unresolved question, tackle more vital information to move forward.

        No long paragraphs. No analogies. No biography. If you can't say it in a bullet, it's not sharp enough yet."""
    else:
        header = f"""Topic: {topic}
        {f'Company: {company}' if company else ''}

        Full session history — all prior rounds and arguments:
        {history_text}

        Relevant context from your research:
        {context if context else '[No specific context retrieved — draw on your frameworks]'}

        Respond to the arguments made so far by {other_names}. You may challenge a specific point, build on an argument, introduce a new angle, or connect this topic to what was already established in earlier rounds."""
        footer = """

        If you agree with a point already made, do not restate it. Deepen it or advance to the next unresolved question.

        No long paragraphs. Little analogies. No biography. If you can't say it in a bullet, it's not sharp enough yet."""

    # Shared sections — identical for the opening turn and every later turn.
    shared = """

        CRITICAL: Read the full debate history before writing. Any point already made by another investor — even if you would frame it differently — must NOT be repeated. Skip it entirely and move to the next unaddressed angle. You will be penalised for restating what has already been said.

        TEMPORAL REASONING — how to use financial data across time:

        All quarters in the financial data are available to you and all of
        them matter — they form the complete picture of how this business
        has evolved. Do not ignore older data.

        Apply this hierarchy when drawing conclusions:

        1. TREND FIRST — before citing any individual quarter, establish the
           direction of the business. Is revenue growth accelerating or
           decelerating? Are margins expanding or compressing? Is FCF improving
           as a percentage of revenue? The trend is more important than any
           single data point.

        2. TRAILING FOUR QUARTERS — this is your primary evidence base for
           the current state of the business. Weight these most heavily when
           making claims about where the company stands today.

        3. INFLECTION QUARTERS — some older quarters matter more than others
           because they represent a turning point. If a key metric changed
           direction in a specific quarter — NRR dropped, margins expanded
           suddenly, revenue growth reaccelerated — that quarter deserves
           explicit mention as the inflection point, not just as historical data.
           Example: "Gross margins compressed from 74% to 71% in Q2 2023 and
           have not recovered — that compression predates the AI infrastructure
           thesis and raises questions about structural pricing power."

        4. OLDER DATA AS CONTEXT — quarters beyond the trailing twelve months
           should be used to establish the baseline the business grew from, or
           to identify whether a current trend is a reversion to historical
           norms or genuinely new behaviour. Never cite an old quarter as
           equivalent evidence to a recent one without explaining why it is
           specifically meaningful.

        If pre-computed trend summaries are available in your retrieved context
        (labelled as TREND ANALYSIS or INFLECTION FLAGS), treat those as
        authoritative — they were computed directly from the raw financial data.

        If the data does not contain a clear inflection point, say so explicitly
        rather than treating all quarters as equally weighted.

        FORMAT RULES — follow exactly:
        1. One conviction sentence — your position, stated plainly
        2. Bullet points — MAXIMUM 5 bullets per turn. Pick your 3-5 sharpest, most specific observations only. If you have more than 5 points, save them for your next turn. Each bullet maximum 20 words. One observation per bullet. No sub-clauses.
        3. If one bullet point is insufficient to cover the point being made, have another bullet point that continues the thought, rather than trying to cram it all into one bullet with conjunctions. This forces clarity and precision in each point.
        4. "Go verify" section — list each verification question on its own line,
           prefixed with "- ". Maximum 3 questions. Each must be specific and
           answerable from filings or product data. Format exactly like this:
           Go verify:
           - What is SBC as % of revenue for the trailing four quarters?
           - What is net revenue retention rate — has it crossed below 115%?"""

    user_message = header + shared + footer

    import time

    for attempt in range(5):
        try:
            response = anthropic_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=DEBATE_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}]
            )
            break
        except anthropic.RateLimitError:
            if attempt < 4:
                print(f"\n  Rate limit hit — waiting 60 seconds before retry ({attempt + 1}/5)...")
                time.sleep(60)
            else:
                raise

    reply = response.content[0].text.strip()
    print(f"\n{reply}\n")

    new_history     = history + [{
        "agent":    agent,
        "turn":     turn,
        "round":    state.get("round", 1),
        "topic":    topic,
        "response": reply,
    }]
    next_index      = (state["agent_index"] + 1) % len(agents)
    total_responses = turns * len(agents)
    finished        = (turn >= total_responses)

    return {
        **state,
        "history":     new_history,
        "turn":        turn + 1,
        "agent_index": next_index,
        "finished":    finished,
    }


# ==============================================================================
# SYNTHESIS NODE
# ==============================================================================

def synthesis_node(state: DebateState) -> DebateState:
    print(f"\n  {'='*56}")
    print(f"  SYNTHESIS")
    print(f"  {'='*56}\n")

    history = state["history"]
    topic   = state["topic"]
    company = state.get("company")
    agents  = state["agents"]
    intent  = state.get("intent", "general")

    # Only synthesise the current round's entries
    current_round = state.get("round", 1)
    round_history = [h for h in history if h.get("round") == current_round and h["agent"] != "synthesis"]

    history_text = "\n\n".join([
        f"{display_name(h['agent'])} (Turn {h['turn']}):\n{h['response']}"
        for h in round_history
    ])

    agents_list = ", ".join(display_name(a) for a in agents)

    synthesis_prompt = f"""You are a neutral senior analyst synthesising a structured investment debate.

Topic: {topic}
{f'Company: {company}' if company else ''}
Participants: {agents_list}
Focus area: {intent}

Current round transcript:
{history_text}

Write a synthesis that:
1. Identifies the core crux of disagreement between the investors on this specific topic
2. Summarises each investor's strongest argument in one sentence
3. Notes which empirical questions would resolve the debate
4. Gives a balanced bottom line — what an investor should take away

Be concise. No more than 4 paragraphs. Do not take sides."""

    response = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=800,
        messages=[{"role": "user", "content": synthesis_prompt}]
    )

    synthesis = response.content[0].text.strip()
    print(synthesis)

    return {
        **state,
        "history": history + [{
            "agent":    "synthesis",
            "turn":     0,
            "round":    current_round,
            "topic":    topic,
            "response": synthesis,
        }]
    }


# ==============================================================================
# ROUTING
# ==============================================================================

def should_continue(state: DebateState) -> str:
    return "synthesis" if state["finished"] else "agent"


# ==============================================================================
# BUILD LANGGRAPH
# ==============================================================================

def build_debate_graph():
    graph = StateGraph(DebateState)
    graph.add_node("agent",     agent_node)
    graph.add_node("synthesis", synthesis_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {
        "agent":     "agent",
        "synthesis": "synthesis",
    })
    graph.add_edge("synthesis", END)
    return graph.compile()


# ==============================================================================
# PDF GENERATION
# ==============================================================================

_MONEY_RE = re.compile(r"^(-?)\$([\d.,]+)([KMB])?$")


def _money_to_float(s: str | None) -> float | None:
    """'$549.0M' -> 549000000.0. Returns None for N/A / unparseable values."""
    if not s:
        return None
    m = _MONEY_RE.match(s.strip())
    if not m:
        return None
    try:
        value = float(m.group(2).replace(",", ""))
    except ValueError:
        return None
    mult = {"K": 1e3, "M": 1e6, "B": 1e9}.get(m.group(3), 1.0)
    return -value * mult if m.group(1) else value * mult


def _grab(pattern: str, text: str) -> str | None:
    """First regex group from text (multiline), or None."""
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _ratio_str(v: str | None) -> str:
    """'11.53' -> '11.5x'. Non-numeric values pass through; None -> 'N/A'."""
    try:
        return f"{float(v):.1f}x"
    except (TypeError, ValueError):
        return v or "N/A"


def _parse_consensus(text: str) -> str | None:
    """Best-effort parse of the yfinance analyst recommendations table into
    'X buy / X hold / X sell'. Returns None if the format doesn't match."""
    try:
        lines = [l for l in text.split("\n") if l.strip()]
        cols, header_idx = None, None
        for i, line in enumerate(lines):
            low = line.lower()
            if "buy" in low and "hold" in low and "sell" in low:
                cols, header_idx = line.split(), i
                break
        if cols is None:
            return None
        for line in lines[header_idx + 1:]:
            parts = line.split()
            if len(parts) < len(cols):
                continue
            vals = dict(zip(cols, parts[-len(cols):]))

            def count(*keys):
                total, found = 0, False
                for col_name, raw in vals.items():
                    if col_name.lower() in keys:
                        try:
                            total += int(float(raw))
                            found = True
                        except ValueError:
                            pass
                return total if found else None

            buys  = count("strongbuy", "buy")
            holds = count("hold")
            sells = count("sell", "strongsell")
            if buys is None and holds is None and sells is None:
                continue

            def show(n):
                return "N/A" if n is None else str(n)

            return f"{show(buys)} buy / {show(holds)} hold / {show(sells)} sell"
    except Exception:
        pass
    return None


def _financial_snapshot_data(company: str | None) -> dict | None:
    """Parse the company's ingested chunks (analyse_company.py output) into the
    Financial Snapshot page fields. Returns None when nothing usable exists —
    the PDF simply skips the page rather than crashing."""
    if not company:
        return None
    col = db[COMPANY_COLLECTION]
    # Latest ingest only — match the retrieval filter so the PDF snapshot and the
    # debate cite the same (most recent) data; older versions remain as history.
    _, latest_version = get_latest_ingest_version(company)
    snap_filter = {"company": company}
    if latest_version:
        snap_filter["ingest_version"] = latest_version
    try:
        docs = list(col.find(snap_filter))
    except Exception:
        return None

    rows = [
        (doc, doc.get("text", ""))
        for doc in docs
        if doc.get("source_type") in ("computed_metrics", "yfinance_financials", "yfinance_metrics")
    ]
    if not rows:
        return None

    ticker = next((m.get("ticker") for m, _ in rows if m.get("ticker")), None)

    def quarterly(form_type: str) -> list[tuple[dict, str]]:
        matches = [
            (m, d) for m, d in rows
            if m.get("form_type") == form_type and "Annual" not in (m.get("source") or "")
        ]
        return sorted(matches, key=lambda md: md[0].get("period", ""), reverse=True)

    cashflow_by_period = {m.get("period"): d for m, d in quarterly("cash_flow")}

    quarters = []
    for m, d in quarterly("income_statement")[:4]:
        period = m.get("period", "")
        cf     = cashflow_by_period.get(period, "")

        rev_str = _grab(r"^Revenue: (\S+)", d)
        op_str  = _grab(r"^Operating Income: (\S+)", d)
        rev_f, op_f = _money_to_float(rev_str), _money_to_float(op_str)
        op_margin = f"{op_f / rev_f * 100:.1f}%" if (rev_f and op_f is not None) else None

        quarters.append({
            "label":        _grab(r"Income Statement (Q\d+ FY\d+)", d) or period or "N/A",
            "revenue":      rev_str or "N/A",
            "yoy":          _grab(r"^Revenue: \S+ \(([+\-][\d.]+%) YoY\)", d),
            "gross_margin": _grab(r"Gross Margin: ([\d.]+%)", d) or "N/A",
            "op_margin":    op_margin or "N/A",
            "sbc":          _grab(r"^Stock-Based Compensation: (\S+)", d) or "N/A",
            "sbc_pct":      _grab(r"^Stock-Based Compensation: \S+ \(([\d.]+%) of revenue\)", d),
            "fcf":          _grab(r"^Free Cash Flow: (\S+)", cf) or "N/A",
            "fcf_margin":   _grab(r"FCF Margin: (-?[\d.]+%)", cf) or "N/A",
        })

    key_metrics = next((d for m, d in rows if m.get("form_type") == "key_metrics"), "")
    market = {
        "market_cap": _grab(r"^Market Cap: (\S+)", key_metrics) or "N/A",
        "ev_revenue": _ratio_str(_grab(r"^EV/Revenue: (\S+)", key_metrics)),
        "forward_pe": _ratio_str(_grab(r"^Forward P/E: (\S+)", key_metrics)),
    }

    rec_text  = next((d for m, d in rows if m.get("form_type") == "analyst_recommendations"), "")
    consensus = _parse_consensus(rec_text) if rec_text else None

    # Pre-computed trend labels, if the computed_metrics chunk carries them
    computed = next((d for m, d in rows if m.get("source_type") == "computed_metrics"), "")
    flags = []
    lines = computed.split("\n")
    i = 0
    while i < len(lines):
        upper = lines[i].upper()
        if "TREND ANALYSIS" in upper or "INFLECTION FLAG" in upper:
            i += 1
            while i < len(lines) and lines[i].strip():
                flags.append(lines[i].strip())
                i += 1
        else:
            i += 1

    if not quarters and not key_metrics and not rec_text and not computed:
        return None

    return {
        "ticker":    ticker,
        "quarters":  quarters,
        "market":    market,
        "consensus": consensus,
        "flags":     flags,
    }


_GV_STOPWORDS = {"what", "is", "the", "and", "of"}

_GV_CATEGORIES = [
    ("VALUATION & RETURNS",   ["valuation", "multiple", "p/e", "fcf", "owner earnings", "yield", "price", "return"]),
    ("COMPETITIVE POSITION",  ["moat", "hyperscaler", "competitor", "churn", "retention", "nrr", "market share", "bundling"]),
    ("FINANCIAL QUALITY",     ["margin", "sbc", "revenue", "gross", "operating", "cash flow", "dilution", "expense"]),
    ("MANAGEMENT & STRATEGY", ["management", "ceo", "capital allocation", "guidance", "strategy", "product"]),
]


def _gv_keywords(question: str) -> set[str]:
    """Significant keywords for dedup — every word bar the stopword list."""
    return set(re.findall(r"[a-z0-9][a-z0-9/\-]*", question.lower())) - _GV_STOPWORDS


def _collect_go_verify(all_rounds: list[dict]) -> dict[str, list[str]]:
    """Extract every 'Go verify' question across all rounds, dedupe by shared
    keywords, and group into research categories for the checklist page."""
    questions: list[str] = []
    for rnd in all_rounds:
        for h in rnd["history"]:
            if h["agent"] == "synthesis":
                continue
            for line in h["response"].split("\n"):
                idx = line.lower().find("go verify")
                if idx == -1:
                    continue
                rest = re.sub(r"^[\s:\*–—\-]+", "", line[idx + len("go verify"):]).strip()
                if not rest:
                    continue
                if "?" in rest:
                    for part in rest.split("?"):
                        part = part.strip(" ;,*-—–")
                        if len(part) > 3:
                            questions.append(f"{part}?")
                else:
                    questions.append(rest)

    # Dedupe: drop any question sharing >= 3 significant keywords with an earlier one
    kept: list[str] = []
    kept_keywords: list[set[str]] = []
    for q in questions:
        kw = _gv_keywords(q)
        if any(len(kw & previous) >= 3 for previous in kept_keywords):
            continue
        kept.append(q)
        kept_keywords.append(kw)

    grouped: dict[str, list[str]] = {name: [] for name, _ in _GV_CATEGORIES}
    grouped["OTHER"] = []
    for q in kept:
        q_low = q.lower()
        for name, keywords in _GV_CATEGORIES:
            if any(k in q_low for k in keywords):
                grouped[name].append(q)
                break
        else:
            grouped["OTHER"].append(q)
    return grouped


def save_pdf(
    all_rounds: list[dict],   # [{round, topic, title, history}]
    agents: list[str],
    company: str | None,
    turns: int,
) -> str:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib import colors
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, HRFlowable, PageBreak,
            Table, TableStyle
        )
        from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
    except ImportError:
        print("\n  ERROR: reportlab not installed.")
        sys.exit(1)

    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    timestamp  = datetime.now().strftime("%Y%m%d_%H%M")
    filename   = f"{timestamp}_session.pdf"
    out_path   = os.path.join(OUTPUTS_DIR, filename)

    doc = SimpleDocTemplate(
        out_path, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )

    base = getSampleStyleSheet()

    def ms(name, parent="Normal", **kw):
        return ParagraphStyle(name, parent=base[parent], **kw)

    s_title       = ms("DocTitle",    "Title",  fontSize=22, leading=28, textColor=colors.HexColor("#1a1a2e"), spaceAfter=4)
    s_subtitle    = ms("DocSub",      "Normal", fontSize=11, textColor=colors.HexColor("#555555"), spaceAfter=2)
    s_meta        = ms("Meta",        "Normal", fontSize=9,  textColor=colors.HexColor("#888888"), spaceAfter=1)
    s_round_title = ms("RoundTitle",  "Normal", fontSize=16, fontName="Helvetica-Bold", textColor=colors.HexColor("#1a1a2e"), spaceBefore=16, spaceAfter=2)
    s_round_sub   = ms("RoundSub",    "Normal", fontSize=9,  textColor=colors.HexColor("#888888"), spaceAfter=8)
    s_turn_header = ms("TurnHeader",  "Normal", fontSize=13, fontName="Helvetica-Bold", textColor=colors.HexColor("#1a1a2e"), spaceBefore=14, spaceAfter=6)
    s_agent_label = ms("AgentLabel",  "Normal", fontSize=10, fontName="Helvetica-Bold", spaceAfter=4)
    s_body        = ms("Body",        "Normal", fontSize=10, leading=15, alignment=TA_JUSTIFY, spaceAfter=10)
    s_synth_hdr   = ms("SynthHdr",    "Normal", fontSize=13, fontName="Helvetica-Bold", textColor=colors.HexColor("#2c3e50"), spaceBefore=14, spaceAfter=6)
    s_synth_body  = ms("SynthBody",   "Normal", fontSize=10, leading=15, alignment=TA_JUSTIFY, textColor=colors.HexColor("#2c3e50"), spaceAfter=10)
    s_bullet     = ms("Bullet",     "Normal", fontSize=10, leading=15,
                   leftIndent=12, spaceAfter=3)
    s_conviction = ms("Conviction", "Normal", fontSize=10, leading=15,
                    fontName="Helvetica-Bold", spaceAfter=6)
    s_go_verify  = ms("GoVerify",   "Normal", fontSize=9,  leading=13,
                    textColor=colors.HexColor("#444444"),
                    fontName="Helvetica-Oblique", spaceAfter=8)
    s_snap_row   = ms("SnapRow",    "Normal", fontSize=10, leading=15, spaceAfter=2)

    def esc(t) -> str:
        return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def agent_label_para(agent: str) -> Paragraph:
        r, g, b = AGENT_COLOURS.get(agent, (0.2, 0.2, 0.2))
        hex_col = "#{:02x}{:02x}{:02x}".format(int(r*255), int(g*255), int(b*255))
        return Paragraph(f'<font color="{hex_col}"><b>{display_name(agent)}</b></font>', s_agent_label)
    
    def render_agent_response(text: str) -> list:
        elements = []
        for line in text.split('\n'):
            line = line.strip()
            if not line or line == '---':
                continue
            conviction_markers = ('conviction:', '**conviction', 'conviction —', 'conviction —')
            if any(line.lower().startswith(m) for m in conviction_markers):
                clean = line.replace('**', '')
                elements.append(Paragraph(clean, s_conviction))
            elif line.startswith('- ') or line.startswith('* '):
                bullet = line[2:].strip()
                bullet = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', bullet)
                bullet = bullet.replace('&', '&amp;')
                elements.append(Paragraph(f'• {bullet}', s_bullet))
            elif line.lower().startswith('**go verify') or line.lower().startswith('go verify'):
                clean = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', line)
                clean = clean.replace('&', '&amp;')
                elements.append(Paragraph(clean, s_go_verify))
            else:
                clean = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', line)
                clean = clean.replace('&', '&amp;')
                elements.append(Paragraph(clean, s_body))
        return elements
    story = []

    # ── Cover ──
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("The Kitchen Table", s_title))
    story.append(Paragraph("Multi-Agent Equity Research Session", s_subtitle))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#1a1a2e"), spaceAfter=8))
    if company:
        story.append(Paragraph(f"<b>Company:</b> {company.title()}", s_meta))
    story.append(Paragraph(f"<b>Participants:</b> {' vs '.join(display_name(a) for a in agents)}", s_meta))
    story.append(Paragraph(f"<b>Date:</b> {datetime.now().strftime('%d %B %Y, %H:%M')}  |  <b>Rounds:</b> {len(all_rounds)}  |  <b>Turns per agent:</b> {turns}", s_meta))
    story.append(Spacer(1, 10*mm))

    # ── Financial Snapshot (parsed from the ingested company chunks) ──
    snapshot = _financial_snapshot_data(company)
    if snapshot:
        story.append(PageBreak())
        ticker_tag = f" ({esc(snapshot['ticker'])})" if snapshot["ticker"] else ""
        story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#1a1a2e"), spaceAfter=6))
        story.append(Paragraph(f"FINANCIAL SNAPSHOT — {esc(company)}{ticker_tag}", s_round_title))
        story.append(Paragraph("Data sourced from ingested financial filings", s_round_sub))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#dddddd"), spaceAfter=8))

        latest  = snapshot["quarters"][0] if snapshot["quarters"] else {}
        yoy_tag = f"   ({esc(latest['yoy'])} YoY)" if latest.get("yoy") else ""
        fcf_tag = f"   ({esc(latest['fcf_margin'])} margin)" if latest.get("fcf_margin") not in (None, "N/A") else ""
        sbc_tag = f"   ({esc(latest['sbc_pct'])} of revenue)" if latest.get("sbc_pct") else ""

        story.append(Paragraph("LATEST QUARTER", s_turn_header))
        for label, value in [
            ("Revenue",          f"{esc(latest.get('revenue', 'N/A'))}{yoy_tag}"),
            ("Gross Margin",     esc(latest.get("gross_margin", "N/A"))),
            ("Operating Margin", esc(latest.get("op_margin", "N/A"))),
            ("FCF",              f"{esc(latest.get('fcf', 'N/A'))}{fcf_tag}"),
            ("SBC",              f"{esc(latest.get('sbc', 'N/A'))}{sbc_tag}"),
        ]:
            story.append(Paragraph(f"<b>{label}:</b> {value}", s_snap_row))

        story.append(Paragraph("MARKET DATA", s_turn_header))
        for label, value in [
            ("Market Cap",        snapshot["market"]["market_cap"]),
            ("EV/Revenue",        snapshot["market"]["ev_revenue"]),
            ("Forward P/E",       snapshot["market"]["forward_pe"]),
            ("Analyst Consensus", snapshot["consensus"] or "N/A"),
        ]:
            story.append(Paragraph(f"<b>{label}:</b> {esc(value)}", s_snap_row))

        story.append(Paragraph("QUARTERLY TREND", s_turn_header))
        if snapshot["quarters"]:
            trend_data = [["Period", "Revenue", "Gr. Margin", "FCF Margin"]]
            for q in snapshot["quarters"]:
                trend_data.append([q["label"], q["revenue"], q["gross_margin"], q["fcf_margin"]])
            trend = Table(trend_data, colWidths=[34*mm, 34*mm, 34*mm, 34*mm], hAlign="LEFT")
            trend.setStyle(TableStyle([
                ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
                ("FONTNAME",      (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE",      (0, 0), (-1, -1), 9),
                ("TEXTCOLOR",     (0, 0), (-1, -1), colors.HexColor("#1a1a2e")),
                ("LINEBELOW",     (0, 0), (-1, 0),  0.5, colors.HexColor("#aaaaaa")),
                ("TOPPADDING",    (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ]))
            story.append(trend)
        else:
            story.append(Paragraph("N/A — no quarterly data available", s_snap_row))

        story.append(Paragraph("TREND FLAGS", s_turn_header))
        if snapshot["flags"]:
            for flag in snapshot["flags"]:
                story.append(Paragraph(f"• {esc(flag)}", s_bullet))
        else:
            story.append(Paragraph("N/A — no pre-computed trend flags in the ingested data", s_snap_row))

        story.append(Spacer(1, 4*mm))
        story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#1a1a2e")))
        story.append(PageBreak())

    # ── Rounds ──
    from collections import defaultdict

    for rnd in all_rounds:
        round_num    = rnd["round"]
        round_topic  = rnd["topic"]
        round_title  = rnd["title"]
        round_history = rnd["history"]

        # Round header
        story.append(PageBreak() if round_num > 1 else Spacer(1, 2*mm))
        story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#1a1a2e"), spaceAfter=6))
        story.append(Paragraph(f"Round {round_num} — {round_title}", s_round_title))
        story.append(Paragraph(f'"{round_topic}"', s_round_sub))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#dddddd"), spaceAfter=8))

        # Group debate entries by turn
        debate_entries = [h for h in round_history if h["agent"] != "synthesis"]
        if not debate_entries:
            continue  # skip empty rounds entirely
        synthesis_entry = next((h for h in round_history if h["agent"] == "synthesis"), None)

        turns_map = defaultdict(list)
        for entry in debate_entries:
            turns_map[entry["turn"]].append(entry)

        for turn_num in sorted(turns_map.keys()):
            story.append(Paragraph(f"Turn {turn_num}", s_turn_header))
            for entry in turns_map[turn_num]:
                story.append(agent_label_para(entry["agent"]))
                for el in render_agent_response(entry["response"]):
                    story.append(el)
                story.append(Spacer(1, 4*mm))

        # Synthesis for this round
        if synthesis_entry:
            story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#2c3e50"), spaceAfter=6))
            story.append(Paragraph("Analyst Synthesis", s_synth_hdr))
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#aaaaaa"), spaceAfter=8))
            synth_text = synthesis_entry["response"]
            synth_text = synth_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            for para in synth_text.split("\n\n"):
                para = para.strip()
                if para:
                    if para.startswith("##"):
                        para = para.lstrip("# ").strip()
                        story.append(Paragraph(f"<b>{para}</b>", s_synth_body))
                    else:
                        story.append(Paragraph(para, s_synth_body))

    # ── Go Verify research checklist (aggregated across all rounds) ──
    go_verify = _collect_go_verify(all_rounds)
    gv_total  = sum(len(items) for items in go_verify.values())
    if gv_total:
        story.append(PageBreak())
        story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#1a1a2e"), spaceAfter=6))
        story.append(Paragraph("GO VERIFY — RESEARCH CHECKLIST", s_round_title))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#dddddd"), spaceAfter=8))
        for category, items in go_verify.items():
            if not items:
                continue
            story.append(Paragraph(category, s_turn_header))
            for q in items:
                story.append(Paragraph(f'<font name="ZapfDingbats">o</font> {esc(q)}', s_bullet))
        story.append(Spacer(1, 4*mm))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#2c3e50"), spaceAfter=4))
        story.append(Paragraph(f"<b>Total items:</b> {gv_total}", s_meta))
        story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#2c3e50"), spaceBefore=2))

    doc.build(story)
    print(f"\n  PDF saved: outputs/{filename}")
    return out_path


# ==============================================================================
# MAIN SESSION LOOP
# ==============================================================================

def run_round(
    topic: str,
    company: str | None,
    agents: list[str],
    turns: int,
    round_num: int,
    session_history: list[dict],
    audit: bool = False,
) -> tuple[list[dict], str]:
    """Run a single debate round and return updated history + synthesis."""

    intent = classify_topic_intent(topic)
    print(f"\n  Intent detected: {intent.upper()}")

    graph = build_debate_graph()

    initial_state: DebateState = {
        "topic":       topic,
        "company":     company,
        "intent":      intent,
        "agents":      agents,
        "turns":       turns,
        "history":     session_history,   # carry full session history in
        "turn":        1,
        "agent_index": 0,
        "round":       round_num,
        "finished":    False,
        "audit":       audit,
    }

    final_state = graph.invoke(initial_state)
    return final_state["history"]


# ==============================================================================
# STREAMING DEBATE (async generator twin of run_round / agent_node)
# ==============================================================================
# These power the backend's token-streaming /debate/start endpoint. agent_node,
# synthesis_node and run_round are deliberately left untouched so the CLI keeps
# working exactly as before; the helpers below replicate their prompt
# construction byte-for-byte (same indentation = same literal whitespace).

def _build_agent_message(
    agent: str,
    topic: str,
    company: str | None,
    history: list[dict],
    agents: list[str],
    intent: str,
    audit: bool = False,
) -> str:
    """Construct an agent's per-turn user message exactly as agent_node() does."""
    context = retrieve_context(topic, agent, company, intent=intent, audit=audit)

    # Full session history — agents see everything from all rounds
    history_text = ""
    if history:
        history_text = "\n\n".join([
            f"{display_name(h['agent'])} (Round {h.get('round',1)}, Turn {h['turn']}):\n{h['response']}"
            for h in history[-12:]
        ])

    others      = [a for a in agents if a != agent]
    other_names = " and ".join(display_name(a) for a in others)

    if not history:
        header = f"""Topic for debate: {topic}
        {f'Company under discussion: {company}' if company else ''}

        Relevant context from your research and knowledge base:
        {context if context else '[No specific context retrieved — draw on your frameworks]'}

        YOUR CONTEXT:
        You are presenting as yourself — your own frameworks, your own voice,
        your own convictions — in a structured investment debate with other
        serious investors. Treat this as a high-stakes internal research
        meeting where a portfolio manager is deciding whether this company
        warrants serious capital allocation.

        Your job is not to be balanced. Your job is to give the sharpest,
        most evidenced version of your actual view. If you are bullish, make
        the bull case with precision. If you are skeptical, name exactly what
        would have to be true for you to be wrong.

        Every claim must be grounded in either retrieved financial data or
        your own retrieved investment philosophy — not general knowledge,
        not industry averages, not what you think sounds right. If you do
        not have the data to support a claim, put it in the Go verify section
        instead of stating it as fact.

        The portfolio manager reading this will verify your key points against
        the filings before making any decision. Your value is in the quality
        of the argument and the precision of what you flag as unresolved —
        not in covering every possible angle."""
        footer = """

        If you agree with a point already made, do not restate it. Deepen it or advance to the next unresolved question, tackle more vital information to move forward.

        No long paragraphs. No analogies. No biography. If you can't say it in a bullet, it's not sharp enough yet."""
    else:
        header = f"""Topic: {topic}
        {f'Company: {company}' if company else ''}

        Full session history — all prior rounds and arguments:
        {history_text}

        Relevant context from your research:
        {context if context else '[No specific context retrieved — draw on your frameworks]'}

        Respond to the arguments made so far by {other_names}. You may challenge a specific point, build on an argument, introduce a new angle, or connect this topic to what was already established in earlier rounds."""
        footer = """

        If you agree with a point already made, do not restate it. Deepen it or advance to the next unresolved question.

        No long paragraphs. Little analogies. No biography. If you can't say it in a bullet, it's not sharp enough yet."""

    # Shared sections — identical for the opening turn and every later turn.
    shared = """

        CRITICAL: Read the full debate history before writing. Any point already made by another investor — even if you would frame it differently — must NOT be repeated. Skip it entirely and move to the next unaddressed angle. You will be penalised for restating what has already been said.

        TEMPORAL REASONING — how to use financial data across time:

        All quarters in the financial data are available to you and all of
        them matter — they form the complete picture of how this business
        has evolved. Do not ignore older data.

        Apply this hierarchy when drawing conclusions:

        1. TREND FIRST — before citing any individual quarter, establish the
           direction of the business. Is revenue growth accelerating or
           decelerating? Are margins expanding or compressing? Is FCF improving
           as a percentage of revenue? The trend is more important than any
           single data point.

        2. TRAILING FOUR QUARTERS — this is your primary evidence base for
           the current state of the business. Weight these most heavily when
           making claims about where the company stands today.

        3. INFLECTION QUARTERS — some older quarters matter more than others
           because they represent a turning point. If a key metric changed
           direction in a specific quarter — NRR dropped, margins expanded
           suddenly, revenue growth reaccelerated — that quarter deserves
           explicit mention as the inflection point, not just as historical data.
           Example: "Gross margins compressed from 74% to 71% in Q2 2023 and
           have not recovered — that compression predates the AI infrastructure
           thesis and raises questions about structural pricing power."

        4. OLDER DATA AS CONTEXT — quarters beyond the trailing twelve months
           should be used to establish the baseline the business grew from, or
           to identify whether a current trend is a reversion to historical
           norms or genuinely new behaviour. Never cite an old quarter as
           equivalent evidence to a recent one without explaining why it is
           specifically meaningful.

        If pre-computed trend summaries are available in your retrieved context
        (labelled as TREND ANALYSIS or INFLECTION FLAGS), treat those as
        authoritative — they were computed directly from the raw financial data.

        If the data does not contain a clear inflection point, say so explicitly
        rather than treating all quarters as equally weighted.

        FORMAT RULES — follow exactly:
        1. One conviction sentence — your position, stated plainly
        2. Bullet points — MAXIMUM 5 bullets per turn. Pick your 3-5 sharpest, most specific observations only. If you have more than 5 points, save them for your next turn. Each bullet maximum 20 words. One observation per bullet. No sub-clauses.
        3. If one bullet point is insufficient to cover the point being made, have another bullet point that continues the thought, rather than trying to cram it all into one bullet with conjunctions. This forces clarity and precision in each point.
        4. "Go verify" section — list each verification question on its own line,
           prefixed with "- ". Maximum 3 questions. Each must be specific and
           answerable from filings or product data. Format exactly like this:
           Go verify:
           - What is SBC as % of revenue for the trailing four quarters?
           - What is net revenue retention rate — has it crossed below 115%?"""

    return header + shared + footer


def _build_synthesis_prompt(
    history: list[dict],
    topic: str,
    company: str | None,
    agents: list[str],
    intent: str,
    current_round: int,
) -> str:
    """Construct the synthesis prompt exactly as synthesis_node() does."""
    # Only synthesise the current round's entries
    round_history = [h for h in history if h.get("round") == current_round and h["agent"] != "synthesis"]

    history_text = "\n\n".join([
        f"{display_name(h['agent'])} (Turn {h['turn']}):\n{h['response']}"
        for h in round_history
    ])

    agents_list = ", ".join(display_name(a) for a in agents)

    synthesis_prompt = f"""You are a neutral senior analyst synthesising a structured investment debate.

Topic: {topic}
{f'Company: {company}' if company else ''}
Participants: {agents_list}
Focus area: {intent}

Current round transcript:
{history_text}

Write a synthesis that:
1. Identifies the core crux of disagreement between the investors on this specific topic
2. Summarises each investor's strongest argument in one sentence
3. Notes which empirical questions would resolve the debate
4. Gives a balanced bottom line — what an investor should take away

Be concise. No more than 4 paragraphs. Do not take sides."""

    return synthesis_prompt


async def stream_debate_round(
    topic: str,
    company: str | None,
    agents: list[str],
    turns: int,
    round_num: int,
    session_history: list[dict],
    audit: bool = False,
):
    """Async-generator twin of run_round(): runs the same debate but streams it
    token by token as event dicts (turn_start / token / turn_end /
    synthesis_start / synthesis_token / synthesis_end / round_complete). Reuses
    agent_node's and synthesis_node's exact prompts via the _build_* helpers."""
    intent  = classify_topic_intent(topic)
    history = list(session_history)          # accumulates this round's turns
    total_responses = turns * len(agents)

    for turn in range(1, total_responses + 1):
        agent         = agents[(turn - 1) % len(agents)]
        name          = display_name(agent)
        system_prompt = load_system_prompt(agent)
        user_message  = _build_agent_message(agent, topic, company, history, agents, intent, audit)

        yield {"type": "turn_start", "agent": agent, "display_name": name, "turn": turn}

        full_response = ""
        for attempt in range(5):
            full_response = ""
            try:
                with anthropic_client.messages.stream(
                    model=CLAUDE_MODEL,
                    max_tokens=DEBATE_MAX_TOKENS,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                ) as stream:
                    for text in stream.text_stream:
                        full_response += text
                        yield {"type": "token", "agent": agent, "token": text}
                break
            except anthropic.RateLimitError:
                if attempt < 4:
                    yield {"type": "status", "agent": agent,
                           "message": f"Rate limit hit — waiting 60 seconds before retry ({attempt + 1}/5)..."}
                    await asyncio.sleep(60)
                else:
                    raise

        reply   = full_response.strip()
        history = history + [{
            "agent":    agent,
            "turn":     turn,
            "round":    round_num,
            "topic":    topic,
            "response": reply,
        }]
        yield {"type": "turn_end", "agent": agent, "turn": turn, "response": reply}

    # ── Synthesis ──
    yield {"type": "synthesis_start"}
    synthesis_prompt = _build_synthesis_prompt(history, topic, company, agents, intent, round_num)

    synthesis = ""
    for attempt in range(5):
        synthesis = ""
        try:
            with anthropic_client.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=800,
                messages=[{"role": "user", "content": synthesis_prompt}],
            ) as stream:
                for text in stream.text_stream:
                    synthesis += text
                    yield {"type": "synthesis_token", "token": text}
            break
        except anthropic.RateLimitError:
            if attempt < 4:
                yield {"type": "status",
                       "message": f"Rate limit hit — waiting 60 seconds before retry ({attempt + 1}/5)..."}
                await asyncio.sleep(60)
            else:
                raise

    synthesis = synthesis.strip()
    history   = history + [{
        "agent":    "synthesis",
        "turn":     0,
        "round":    round_num,
        "topic":    topic,
        "response": synthesis,
    }]
    yield {"type": "synthesis_end", "response": synthesis}
    yield {"type": "round_complete", "history": history}


def main():
    parser = argparse.ArgumentParser(
        description="Kitchen Table — Multi-Agent Equity Debate Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  py -3.11 main.py --topic "Is Datadog a good buy?" --company datadog
  py -3.11 main.py --topic "Is Datadog a good buy?" --agents buffett cathie_wood peter_lynch
  py -3.11 main.py --topic "Is Datadog a good buy?" --agents buffett peter_lynch --turns 3 --first peter_lynch
        """
    )
    parser.add_argument("--topic",   type=str)
    parser.add_argument("--company", type=str)
    parser.add_argument("--agents",  type=str, nargs="+")
    parser.add_argument("--turns",   type=int, default=DEFAULT_TURNS)
    parser.add_argument("--first",   type=str, default=None)
    parser.add_argument("--audit",   action="store_true",
                        help="Print a retrieval audit report for each agent before it speaks")
    args = parser.parse_args()

    # ── Topic ──
    topic = args.topic
    if not topic:
        print("\n  KITCHEN TABLE — Multi-Agent Equity Research Session")
        print("  " + "=" * 54)
        topic = input("\n  Enter first debate topic: ").strip()
        if not topic:
            print("  No topic provided. Exiting.")
            sys.exit(1)

    # ── Agents ──
    agents = [a.lower().replace(" ", "_") for a in args.agents] if args.agents else DEFAULT_AGENTS

    # ── First speaker ──
    if args.first:
        first = args.first.lower().replace(" ", "_")
        if first not in agents:
            print(f"\n  ERROR: --first '{first}' is not in the agents list: {agents}")
            sys.exit(1)
        idx    = agents.index(first)
        agents = agents[idx:] + agents[:idx]

    turns   = args.turns
    company = normalize_company(args.company) if args.company else None

    # ── Validate agents ──
    for agent in agents:
        if not os.path.exists(system_prompt_path(agent)):
            print(f"\n  ERROR: Missing system prompt for {agent}")
            sys.exit(1)

    # ── Validate company exists in the database ──
    if company:
        companies = available_companies()
        if company not in companies:
            print(f"\n  ERROR: Company '{company}' does not exist in the database.")
            if companies:
                print(f"  Available: {', '.join(companies)}")
            else:
                print(f"  The company_financials collection is empty.")
            print(f"  Ingest it first: py -3.11 scripts/ingest_company.py --folder <company>_raw")
            sys.exit(1)

    # ── Session loop ──
    session_history = []   # accumulates all rounds
    all_rounds      = []   # for PDF: [{round, topic, title, history}]
    round_num       = 1

    try:
        while True:
            print(f"\n  {'='*56}")
            print(f"  KITCHEN TABLE — ROUND {round_num}")
            print(f"  {'='*56}")
            print(f"  Topic      : {topic}")
            print(f"  Company    : {company or 'None'}")
            print(f"  Agents     : {' vs '.join(display_name(a) for a in agents)}")
            print(f"  Turns each : {turns}  ({turns * len(agents)} total responses)")
            print(f"  {'='*56}")

            # Generate clean section title for PDF
            round_title = generate_round_title(topic, anthropic_client)
            print(f"  Section    : {round_title}")

            # Run the round — passes full session history in
            session_history = run_round(
                topic, company, agents, turns, round_num, session_history, audit=args.audit
            )

            # Extract just this round's entries for PDF
            round_history = [h for h in session_history if h.get("round") == round_num]
            if round_history:  # only add if there's actual content
                all_rounds.append({
                    "round":   round_num,
                    "topic":   topic,
                    "title":   round_title,
                    "history": round_history,
                })

            print(f"\n  {'='*56}")
            print(f"  ROUND {round_num} COMPLETE")
            print(f"  {'='*56}")
            print(f"\n  Type your next topic to continue, or 'quit' / 'stop' to save and exit.")
            print(f"  > ", end="", flush=True)

            next_input = input().strip()

            if next_input.lower() in ("quit", "stop", "exit", "q", "done"):
                break

            if not next_input:
                print("  No topic entered. Type 'quit' to exit or enter a topic to continue.")
                print(f"  > ", end="", flush=True)
                next_input = input().strip()
                if not next_input or next_input.lower() in ("quit", "stop", "exit", "q", "done"):
                    break

            topic     = next_input
            round_num += 1

    except KeyboardInterrupt:
        print("\n\n  Session interrupted — saving PDF...")
    finally:
        if all_rounds:
            save_pdf(all_rounds, agents, company, turns)
            print(f"\n  {'='*56}")
            print(f"  SESSION SAVED — {len(all_rounds)} round(s)")
            print(f"  {'='*56}\n")
        else:
            print("\n  No rounds completed — nothing to save.")


if __name__ == "__main__":
    main()