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
import argparse
import anthropic
import chromadb
from datetime import datetime
from pathlib import Path
from sentence_transformers import SentenceTransformer
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
    AGENT_DISPLAY, AGENT_COLOURS, normalize_company
)

# ==============================================================================
# DEBATE SETTINGS (defaults — all overridable via CLI)
# ==============================================================================

DEFAULT_AGENTS      = ["buffett", "cathie_wood"]
DEFAULT_TURNS       = 5
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


def build_focus_instruction(intent: str, company: str | None) -> str:
    company_str = company.title() if company else "the company"
    instructions = {
        "financials": (
            f"Focus strictly on {company_str}'s financial data: revenue, margins, "
            f"cash flow, stock-based compensation, earnings quality, and valuation. "
            f"Ground every claim in specific numbers from the financial data provided. "
            f"Do not reference your personal history or past investments."
        ),
        "growth": (
            f"Focus strictly on {company_str}'s growth trajectory: customer expansion, "
            f"net revenue retention, TAM, product pipeline, and compounding dynamics. "
            f"Use specific metrics from the data provided. "
            f"Do not reference your personal history or past investments."
        ),
        "competitive": (
            f"Focus strictly on {company_str}'s competitive position: moat durability, "
            f"switching costs, market share, and specific competitive threats. "
            f"Name the specific competitors and dynamics at play. "
            f"Where relevant, tie in what was established about the financials earlier. "
            f"Do not reference your personal history or past investments."
        ),
        "macro": (
            f"Focus strictly on how the macro environment affects {company_str} specifically. "
            f"Be concrete about the mechanisms. Where relevant, connect to the financial "
            f"and competitive points already established in this session. "
            f"Do not reference your personal history or past investments."
        ),
        "management": (
            f"Focus strictly on {company_str}'s management quality, capital allocation, "
            f"and strategic execution. Use specific decisions and data points. "
            f"Connect to the financial and competitive context already established. "
            f"Do not reference your personal history or past investments."
        ),
    }
    return instructions.get(
        intent,
        f"Stay strictly on the topic and {company_str} at hand. "
        f"Do not reference your personal history, past funds, or past investments. "
        f"Apply your framework to this specific company using the data provided. "
        f"Where relevant, build on what has already been established in this session."
    )


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
embed_model      = SentenceTransformer(EMBED_MODEL)
chroma_client    = chromadb.PersistentClient(path=CHROMA_DIR)
anthropic_client = anthropic.Anthropic()


def load_collection(name: str):
    try:
        return chroma_client.get_collection(name)
    except Exception:
        return None


def available_companies() -> list[str]:
    """Distinct company names currently ingested in the financials collection."""
    col = load_collection(COMPANY_COLLECTION)
    if col is None:
        return []
    data = col.get(include=["metadatas"])
    return sorted({m.get("company") for m in data["metadatas"] if m.get("company")})


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
    """
    Core retrieval. Returns one structured record per retrieved chunk plus the
    expansion queries used. Both retrieve_context (which builds the debate
    prompt) and print_retrieval_report (the --audit view + scripts/audit_rag.py)
    build on this, so the engine and the auditor can never diverge.
    """
    expansions = build_expansions(query, intent, company)
    comp_results_per_query = 3 if intent in ("financials", "growth") else 2

    phil_collection = load_collection(philosophy_collection(agent))
    comp_collection = load_collection(COMPANY_COLLECTION) if company else None

    seen_ids: set = set()
    records: list[dict] = []

    for q in expansions:
        embedding = embed_model.encode(q).tolist()

        if phil_collection:
            try:
                results = phil_collection.query(
                    query_embeddings=[embedding],
                    n_results=min(3, phil_collection.count()),
                )
                for i, doc in enumerate(results["documents"][0]):
                    doc_id = results["ids"][0][i]
                    if doc_id not in seen_ids:
                        seen_ids.add(doc_id)
                        meta = results["metadatas"][0][i] or {}
                        records.append({
                            "collection": "philosophy",
                            "source":     meta.get("source", "philosophy"),
                            "filename":   meta.get("filename", ""),
                            "doc":        doc,
                        })
            except Exception:
                pass

        if comp_collection and company:
            try:
                results = comp_collection.query(
                    query_embeddings=[embedding],
                    n_results=min(comp_results_per_query, comp_collection.count()),
                    where={"company": company} if company else None,
                )
                for i, doc in enumerate(results["documents"][0]):
                    doc_id = results["ids"][0][i]
                    if doc_id not in seen_ids:
                        seen_ids.add(doc_id)
                        meta = results["metadatas"][0][i] or {}
                        records.append({
                            "collection": "company",
                            "source":     meta.get("source", f"{company} financials"),
                            "filename":   meta.get("filename", ""),
                            "doc":        doc,
                        })
            except Exception:
                pass

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
    focus_instruction = build_focus_instruction(intent, company)

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
        user_message = f"""Topic for debate: {topic}
        {f'Company under discussion: {company}' if company else ''}

        Relevant context from your research and knowledge base:
        {context if context else '[No specific context retrieved — draw on your frameworks]'}

        {focus_instruction}

        YOUR ROLE: 
        You are a top-tier investment analyst working at a prestigious hedge fund, presenting to your Portfolio Manager 
        State your investment conviction on this topic in a single sentence. Then support it with 3-5 bullet points — each one a specific, standalone observation. 
        These can be metrics, product facts, competitive dynamics, or management calls. 
        Be specific enough that an investor could verify them or act on them. 
        If you agree with a point already made by the other investors, do not restate it — deepen it or advance to the next unresolved question. 
        End with a "Go verify" line that lists the exact questions this raises that need to be checked against the filings or the product.

        At the end of the day, the goal is to be able to initiate coverage of a stock as quick as possible based on the arguments that are put forth. 
        The portfolio manager will take your arguments, verify the key points, and then decide whether to continue coverage of the stock based on the strength of the case and the unresolved questions.

        FORMAT RULES — follow exactly:
        1. One conviction sentence — your position, stated plainly
        2. Bullet points — maximum 20 words each. One observation per bullet. No sub-clauses, no qualifications, no "but". If it needs more than 20 words it is two bullets, not one long one.
        3. If one bullet point is insufficient to cover the point being made, have another bullet point that continues the thought, rather than trying to cram it all into one bullet with conjunctions. This forces clarity and precision in each point.
        4. One "Go verify" line — the exact questions this raises that need to be checked against the filings or the product

        If you agree with a point already made, do not restate it. Deepen it or advance to the next unresolved question, tackle more vital information to move forward.

        No long paragraphs. No analogies. No biography. If you can't say it in a bullet, it's not sharp enough yet."""
    else:
        user_message = f"""Topic: {topic}
        {f'Company: {company}' if company else ''}

        Full session history — all prior rounds and arguments:
        {history_text}

        Relevant context from your research:
        {context if context else '[No specific context retrieved — draw on your frameworks]'}

        {focus_instruction}

        Respond to the arguments made so far by {other_names}. You may challenge a specific point, build on an argument, introduce a new angle, or connect this topic to what was already established in earlier rounds.

        FORMAT RULES — follow exactly:
        1. One conviction sentence — your position, stated plainly
        2. Bullet points — each one a specific, standalone observation. Can be a metric, a product fact, a competitive dynamic, or a management call. Must be specific enough to verify or act on.
        3. One "Go verify" line — the exact questions this raises that need to be checked against the filings or the product

        If you agree with a point already made, do not restate it. Deepen it or advance to the next unresolved question.

        No long paragraphs. Little analogies. No biography. If you can't say it in a bullet, it's not sharp enough yet."""

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
            SimpleDocTemplate, Paragraph, Spacer, HRFlowable, PageBreak
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
            if line.startswith('**Conviction') or line.startswith('**conviction'):
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
        if load_collection(philosophy_collection(agent)) is None:
            print(f"\n  ERROR: No philosophy collection for {agent}")
            print(f"  Run: py -3.11 scripts/ingest_philosophy.py --agent {agent}")
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