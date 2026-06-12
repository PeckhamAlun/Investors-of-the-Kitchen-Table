"""
==============================================================================
  KNOWLEDGE_AUDIT.PY — KNOWLEDGE COVERAGE AUDIT (MongoDB Atlas + Gemini)
  Comprehensive coverage audit of every agent's philosophy collection in
  MongoDB Atlas. For each agent it:

    1. Generates a custom 35-topic taxonomy from the agent's own corpus (Claude)
    2. Scores coverage of each topic (dynamic + a universal benchmark set)
    3. Assesses debate readiness (bull vs bear framing) per dynamic topic
    4. Analyses source diversity + temporal range
    5. (all-agent mode) detects inter-agent retrieval overlap
    6. Builds a vocabulary fingerprint of each agent's distinctive terms

  This is the MongoDB/Gemini counterpart of scripts/knowledge_audit.py. The
  scoring logic is byte-for-byte identical; only the data fetching changes —
  Gemini embeddings instead of all-MiniLM-L6-v2, and MongoDB Atlas
  ($vectorSearch + find) instead of ChromaDB.

  Read-only against MongoDB Atlas. The only outputs are the printed report and,
  with --output, a saved .txt copy.

==============================================================================

  USAGE:
      py -3.11 migration/knowledge_audit.py
      py -3.11 migration/knowledge_audit.py --agent buffett
      py -3.11 migration/knowledge_audit.py --output

  PERFORMANCE:
      ~2 Claude API calls per agent (taxonomy + batched debate framings), so
      ~6 calls for 3 agents, plus many Gemini embeddings + MongoDB $vectorSearch
      queries. Agents run sequentially to avoid rate limits. Every Claude call
      degrades gracefully — on failure the agent falls back to the universal
      topic set and the audit continues.

==============================================================================
"""

import os
import sys
import re
import json
import random
import argparse
from datetime import datetime
from collections import Counter, defaultdict
from statistics import mean

import anthropic
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from google import genai as google_genai

# UTF-8 so the emoji / box-drawing report survives legacy consoles (Windows cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ──────────────────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────────────────

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from config import (
    MONGODB_URI, MONGODB_DB_NAME,
    GEMINI_EMBED_MODEL, GOOGLE_API_KEY,
    AGENT_REGISTRY, mongo_philosophy_collection,
    CLAUDE_MODEL, OUTPUTS_DIR,
)

# ──────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────────────────

UNIVERSAL_TOPICS = [
    "competitive moat durable advantage pricing power",
    "capital allocation buybacks dividends acquisitions",
    "management quality integrity owner operator",
    "valuation intrinsic value margin of safety",
    "free cash flow owner earnings quality",
    "growth investing TAM expansion compounding",
    "risk management downside permanent loss capital",
    "technology disruption platform network effects",
    "AI artificial intelligence infrastructure investment",
    "macro environment interest rates inflation cycle",
    "software SaaS recurring revenue business model",
    "financial services banking insurance economics",
    "consumer brand retail loyalty pricing",
    "cryptocurrency digital assets blockchain",
    "debt leverage financial risk balance sheet",
]

TAXONOMY_SIZE      = 35
TAXONOMY_SAMPLE    = 60     # chunks sampled to seed taxonomy generation
VOCAB_SAMPLE       = 200    # chunks sampled for the vocabulary fingerprint
CHUNK_PREVIEW_CHARS = 500   # truncation per chunk when seeding the taxonomy prompt
N_RESULTS          = 5
STRONG_THRESHOLD   = 70     # coverage score considered "strong"

# Debate-readiness calibration (looser than STRONG_THRESHOLD — bull/bear framings
# are narrower queries and naturally score a bit lower).
DEBATE_READY_MIN   = 55     # both sides must clear this for DEBATE READY
ONE_SIDED_HIGH     = 65     # one side this strong...
ONE_SIDED_LOW      = 55     # ...while the other is below this → ONE-SIDED

# Lightweight keyword buckets for grouping the dynamic taxonomy in the report.
CATEGORY_KEYWORDS = [
    ("Valuation", ["valuation", "intrinsic", "multiple", "dcf", "discount",
                   "margin of safety", "earnings power", "price to", "ev/"]),
    ("Risk", ["risk", "downside", "permanent loss", "drawdown", "volatility",
              "hedge", "leverage", "debt", "balance sheet"]),
    ("Modern & Disruption", ["ai", "artificial intelligence", "crypto", "blockchain",
                             "platform", "network effect", "disrupt", "innovation",
                             "digital", "autonomous", "genomic", "robot"]),
    ("Macro & Markets", ["macro", "interest rate", "inflation", "cycle", "fed",
                         "recession", "monetary", "rates", "economy"]),
    ("Management & Capital Allocation", ["management", "capital allocation", "buyback",
                                         "dividend", "acquisition", "ceo", "founder",
                                         "owner operator", "governance", "incentive"]),
    ("Sectors & Industries", ["software", "saas", "semiconductor", "bank", "insurance",
                              "retail", "consumer", "energy", "healthcare", "biotech",
                              "auto", "fintech", "cloud", "industry", "sector"]),
    ("Growth", ["growth", "tam", "compounding", "scaling", "expansion", "hypergrowth",
                "retention", "land and expand"]),
    ("Philosophy & Frameworks", ["philosophy", "framework", "principle", "temperament",
                                 "patience", "circle of competence", "long-term",
                                 "quality", "value investing", "approach"]),
]

# Compact stopword set for the vocabulary fingerprint.
STOPWORDS = set("""
the a an and or but if then than that this these those is are was were be been being
to of in on for with as at by from into about over under again further once here there
all any both each few more most other some such no nor not only own same so too very
can will just don should now we you they he she it i me my our your their his her them us
have has had having do does did doing would could may might must shall not its it's
one two three four five year years company companies business businesses market markets
also been more like get got make made much many lot well good great new time people
which who whom whose what when where why how because while during before after above below
they're we're you're i'm he's she's there's that's what's let us per via etc among within
""".split())


# ──────────────────────────────────────────────────────────────────────────
# INITIALISE SHARED RESOURCES (same pattern as migration/ingest_philosophy.py)
# ──────────────────────────────────────────────────────────────────────────

print("\n  Initialising Knowledge Audit...", file=sys.stderr, flush=True)

if not GOOGLE_API_KEY:
    print("\n  ERROR: GOOGLE_API_KEY is not set (.env). Cannot embed.", file=sys.stderr, flush=True)
    sys.exit(1)
if not MONGODB_URI:
    print("\n  ERROR: MONGODB_URI is not set (.env). Cannot connect.", file=sys.stderr, flush=True)
    sys.exit(1)

gemini_client    = google_genai.Client(api_key=GOOGLE_API_KEY)
mongo_client     = MongoClient(MONGODB_URI, server_api=ServerApi('1'))
db               = mongo_client[MONGODB_DB_NAME]
anthropic_client = anthropic.Anthropic()

# Known philosophy collections come from the registry; we confirm each one
# actually exists (and is non-empty) in Atlas before auditing it. This replaces
# ChromaDB's chroma_client.list_collections().
_DB_COLLECTIONS = set(db.list_collection_names())


# Report buffer — emit() prints AND accumulates for the optional --output file.
report_lines: list[str] = []


def emit(line: str = "") -> None:
    report_lines.append(line)
    print(line)


def log(msg: str) -> None:
    """Progress messages — go to stderr so they don't pollute the saved report."""
    print(msg, file=sys.stderr, flush=True)


# ──────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────

def display_name(agent: str) -> str:
    info = AGENT_REGISTRY.get(agent)
    return info["display"] if info else agent.replace("_", " ").title()


def load_collection(name: str):
    """Return the MongoDB collection if it exists and is non-empty, else None.

    MongoDB lazily creates collections on first insert, so an unbuilt agent has
    no collection at all — mirror ChromaDB's "missing collection → None".
    """
    if name in _DB_COLLECTIONS and db[name].count_documents({}) > 0:
        return db[name]
    return None


def embed_query(text: str) -> list:
    """Embed a single query string with Gemini, returning a vector (list of floats)."""
    result = gemini_client.models.embed_content(
        model=GEMINI_EMBED_MODEL,
        contents=text,
    )
    return list(result.embeddings[0].values)


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    return t


def parse_json_array(text: str) -> list:
    """Best-effort extraction of a JSON array of strings from a Claude reply."""
    t = _strip_fences(text)
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON array found")
    arr = json.loads(t[start:end + 1])
    return [str(x).strip() for x in arr if str(x).strip()]


def parse_json_objects(text: str) -> list:
    """Extract a JSON array of objects from a Claude reply."""
    t = _strip_fences(text)
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON array found")
    return json.loads(t[start:end + 1])


def status_for(score: float) -> tuple[str, str]:
    if score >= 70:
        return ("✅", "STRONG")
    if score >= 40:
        return ("⚠️", "MODERATE")
    if score >= 10:
        return ("⚠️", "WEAK")
    return ("❌", "GAP")


def categorise(topic: str) -> str:
    t = topic.lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(kw in t for kw in keywords):
            return category
    return "Other"


def tokenise(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z\-']{3,}", text.lower())
    return [w for w in words if w not in STOPWORDS]


# ──────────────────────────────────────────────────────────────────────────
# STEP 1 — DYNAMIC TAXONOMY
# ──────────────────────────────────────────────────────────────────────────

def generate_taxonomy(agent: str, docs: list[str]) -> tuple[list[str], bool]:
    """
    Generate a custom 35-topic taxonomy from the agent's corpus via Claude.
    Returns (topics, used_fallback). On any failure, falls back to the
    universal topic set and returns used_fallback=True.
    """
    sample = random.sample(docs, min(TAXONOMY_SAMPLE, len(docs)))
    sampled_text = "\n\n".join(d[:CHUNK_PREVIEW_CHARS] for d in sample)
    name = display_name(agent)

    prompt = f"""You are analysing an investor's knowledge base to generate a comprehensive audit taxonomy.

Here are sample excerpts from {name}'s philosophy collection:

{sampled_text}

Generate exactly {TAXONOMY_SIZE} topic query strings that would test comprehensive coverage of this investor's knowledge domain. Cover:
- Their core investment philosophy and frameworks
- Sectors and industries they have deep expertise in
- Macro and market dynamics they care about
- Valuation methodologies they use
- Risk frameworks they apply
- Topics they SHOULD have views on given their background (even if potentially absent)
- Modern topics like AI infrastructure, crypto, platform economics that any serious investor needs a position on

Return ONLY a JSON array of {TAXONOMY_SIZE} strings. No preamble, no explanation, no markdown. Example format:
["topic query one", "topic query two", ...]"""

    try:
        resp = anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        topics = parse_json_array(resp.content[0].text)
        if not topics:
            raise ValueError("empty taxonomy")
        return topics[:TAXONOMY_SIZE], False
    except Exception as e:
        log(f"      WARNING: taxonomy generation failed ({e}); falling back to universal topics")
        return list(UNIVERSAL_TOPICS), True


# ──────────────────────────────────────────────────────────────────────────
# STEP 2 — COVERAGE SCORING
# ──────────────────────────────────────────────────────────────────────────

def score_topic(collection, query: str) -> dict:
    """Score one topic's coverage in a collection. 0–100 coverage score.

    MongoDB $vectorSearch returns a similarity score directly (vectorSearchScore)
    rather than a distance, so avg_sim is the mean of those scores — the rest of
    the scoring formula is identical to scripts/knowledge_audit.py.
    """
    embedding = embed_query(query)
    try:
        n = min(N_RESULTS, collection.count_documents({}))
        if n <= 0:
            raise ValueError("empty collection")
        results = list(collection.aggregate([
            {
                "$vectorSearch": {
                    "index": "vector_index",
                    "path": "embedding",
                    "queryVector": embedding,
                    "numCandidates": 50,
                    "limit": n,
                }
            },
            {
                "$project": {
                    "text": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]))
    except Exception:
        return {"query": query, "count": 0, "similarity": 0.0,
                "score": 0.0, "symbol": "❌", "label": "GAP", "docs": []}

    docs    = [r.get("text", "") for r in results]
    sims    = [r.get("score", 0.0) for r in results]
    count   = len(docs)
    avg_sim = mean(sims) if sims else 0.0
    score   = max(0.0, min(100.0, (count / N_RESULTS) * avg_sim * 100))
    symbol, label = status_for(score)
    return {"query": query, "count": count, "similarity": avg_sim,
            "score": score, "symbol": symbol, "label": label, "docs": docs}


# ──────────────────────────────────────────────────────────────────────────
# STEP 3 — DEBATE READINESS (batched framing — one Claude call per agent)
# ──────────────────────────────────────────────────────────────────────────

def generate_framings(agent: str, topics: list[str]) -> dict:
    """
    One batched Claude call producing a bull + bear search query per topic.
    (The spec describes a per-topic call; we batch to respect the API budget
    and rate limits — same result, ~1 call instead of 35.) Returns
    {topic: {"bull": str, "bear": str}}. Empty dict on failure.
    """
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(topics))
    prompt = (
        "For each investment topic below, generate a concise bullish argument "
        "search query and a concise bearish argument search query (5-12 words each).\n\n"
        f"Topics:\n{numbered}\n\n"
        'Return ONLY a JSON array; each element {"topic": "<exact topic text>", '
        '"bull": "...", "bear": "..."}. No preamble, no markdown.'
    )
    try:
        resp = anthropic_client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        objs = parse_json_objects(resp.content[0].text)
        framings = {}
        for o in objs:
            t = str(o.get("topic", "")).strip()
            if t and o.get("bull") and o.get("bear"):
                framings[t] = {"bull": str(o["bull"]), "bear": str(o["bear"])}
        return framings
    except Exception as e:
        log(f"      WARNING: debate framing generation failed ({e}); skipping debate readiness")
        return {}


def debate_status(collection, framing: dict) -> tuple[str, float, float]:
    bull = score_topic(collection, framing["bull"])["score"]
    bear = score_topic(collection, framing["bear"])["score"]
    if bull > DEBATE_READY_MIN and bear > DEBATE_READY_MIN:
        return ("🎯 DEBATE READY", bull, bear)
    if ((bull > ONE_SIDED_HIGH and bear < ONE_SIDED_LOW) or
            (bear > ONE_SIDED_HIGH and bull < ONE_SIDED_LOW)):
        return ("⚡ ONE-SIDED", bull, bear)
    return ("🔇 BLIND SPOT", bull, bear)


# ──────────────────────────────────────────────────────────────────────────
# STEP 4 — SOURCE DIVERSITY
# ──────────────────────────────────────────────────────────────────────────

def analyse_sources(metadatas: list[dict]) -> dict:
    total = len(metadatas)
    counts = Counter(m.get("filename") or m.get("source") or "unknown" for m in metadatas)

    # Temporal: scan distinct source labels + filenames for 4-digit years.
    year_re = re.compile(r"(?:19|20)\d{2}")
    distinct_sources = {(m.get("source") or "") + " " + (m.get("filename") or "")
                        for m in metadatas}
    years = []
    for s in distinct_sources:
        years.extend(int(y) for y in year_re.findall(s))

    decade_flag = None
    if years:
        decades = Counter((y // 10) * 10 for y in years)
        top_decade, top_n = decades.most_common(1)[0]
        if top_n / len(years) > 0.70:
            decade_flag = f"{top_decade}s ({top_n}/{len(years)} dated sources)"

    return {
        "total": total,
        "counts": counts,
        "unique": len(counts),
        "years": years,
        "decade_flag": decade_flag,
    }


def emit_source_diversity(div: dict) -> None:
    emit("📚 SOURCE DIVERSITY")
    total = div["total"]
    for src, n in div["counts"].most_common():
        pct = (n / total * 100) if total else 0
        flag = "  ⚠️  HIGH CONCENTRATION" if pct > 60 else ""
        emit(f"   {src[:40]:<40} {n:>6,}  ({pct:>2.0f}%){flag}")
    if div["unique"] < 3:
        emit("   ⚠️  NARROW TRAINING BASE — fewer than 3 distinct sources")
    if div["years"]:
        emit(f"   Temporal range: {min(div['years'])} – {max(div['years'])}")
        if div["decade_flag"]:
            emit(f"   ⚠️  TEMPORAL SKEW — {div['decade_flag']}")
    emit()


# ──────────────────────────────────────────────────────────────────────────
# STEP 6 — VOCABULARY FINGERPRINT
# ──────────────────────────────────────────────────────────────────────────

def term_frequencies(docs: list[str]) -> dict:
    """Normalised term frequency (count / total tokens) for a sample of docs."""
    sample = random.sample(docs, min(VOCAB_SAMPLE, len(docs)))
    tokens = []
    for d in sample:
        tokens.extend(tokenise(d))
    total = len(tokens) or 1
    counts = Counter(tokens)
    return {term: n / total for term, n in counts.items()}


def vocabulary_fingerprint(agent: str, all_freqs: dict[str, dict]) -> list[str]:
    """Top distinctive terms for `agent` vs the average across other agents."""
    own = all_freqs.get(agent, {})
    others = [f for a, f in all_freqs.items() if a != agent]

    def other_avg(term: str) -> float:
        if not others:
            return 0.0
        return mean(f.get(term, 0.0) for f in others)

    scored = [(term, freq - other_avg(term)) for term, freq in own.items()]
    scored.sort(key=lambda kv: kv[1], reverse=True)
    return [term for term, _ in scored[:10]]


# ──────────────────────────────────────────────────────────────────────────
# PER-AGENT AUDIT
# ──────────────────────────────────────────────────────────────────────────

def audit_agent(agent: str, collection, docs: list[str], metadatas: list[dict],
                all_freqs: dict, universal_overlap: dict) -> dict:
    name = display_name(agent)
    div = analyse_sources(metadatas)

    emit("=" * 56)
    emit(f"{name.upper()}  —  {collection.count_documents({}):,} docs  |  {div['unique']} sources")
    emit("=" * 56)
    emit()

    # ── Source diversity ──
    emit_source_diversity(div)

    # ── Vocabulary fingerprint ──
    log(f"      Building vocabulary fingerprint...")
    fingerprint = vocabulary_fingerprint(agent, all_freqs)
    emit("🔤 VOCABULARY FINGERPRINT  (most distinctive terms vs other agents)")
    if fingerprint:
        emit("   " + " · ".join(fingerprint[:5]))
        if len(fingerprint) > 5:
            emit("   " + " · ".join(fingerprint[5:10]))
    else:
        emit("   (insufficient data)")
    emit()

    # ── Dynamic taxonomy ──
    log(f"      Generating dynamic taxonomy (Claude)...")
    taxonomy, used_fallback = generate_taxonomy(agent, docs)

    log(f"      Scoring {len(taxonomy)} dynamic topics...")
    dynamic_scores = [score_topic(collection, t) for t in taxonomy]

    header = "📊 DYNAMIC TOPIC COVERAGE  (generated from corpus)"
    if used_fallback:
        header += "  [FALLBACK: universal topics — taxonomy generation failed]"
    emit(header)
    grouped = defaultdict(list)
    for s in dynamic_scores:
        grouped[categorise(s["query"])].append(s)
    for category in sorted(grouped):
        emit(f"   {category}")
        for s in grouped[category]:
            emit(f"      {s['symbol']} {s['score']:>5.0f}  {s['label']:<8} {s['query'][:52]}")
    emit()

    # ── Universal topic coverage (also feeds the cross-agent matrix) ──
    log(f"      Scoring {len(UNIVERSAL_TOPICS)} universal topics...")
    universal_scores = {}
    for t in UNIVERSAL_TOPICS:
        s = score_topic(collection, t)
        universal_scores[t] = s
        # Record retrieved chunk texts for inter-agent overlap detection.
        universal_overlap[t][agent] = set(s["docs"])
    emit("📊 UNIVERSAL TOPIC COVERAGE  (cross-agent benchmark)")
    emit("   Note: universal topics use generic query strings and score lower than")
    emit("   corpus-specific dynamic topics — MODERATE on universal benchmarks is")
    emit("   expected and normal for a well-trained agent.")
    for t in UNIVERSAL_TOPICS:
        s = universal_scores[t]
        emit(f"   {s['symbol']} {s['score']:>5.0f}  {s['label']:<8} {t}")
    emit()

    # ── Debate readiness (batched framing) ──
    log(f"      Generating debate framings (Claude) + scoring...")
    framings = generate_framings(agent, taxonomy)
    emit("🎯 DEBATE READINESS")
    if not framings:
        emit("   (skipped — framing generation unavailable)")
    else:
        for t in taxonomy:
            f = framings.get(t)
            if not f:
                continue
            status, bull, bear = debate_status(collection, f)
            emit(f"   {status:<16} bull {bull:>3.0f} / bear {bear:>3.0f}   {t[:46]}")
    emit()

    # ── Gaps & recommendations ──
    weak = [s for s in dynamic_scores + list(universal_scores.values())
            if s["label"] in ("WEAK", "GAP")]
    emit("⚠️  GAPS & RECOMMENDATIONS")
    if not weak:
        emit("   None — coverage is strong or moderate across all audited topics.")
    else:
        # De-dup by query, worst first.
        seen = {}
        for s in weak:
            if s["query"] not in seen or s["score"] < seen[s["query"]]["score"]:
                seen[s["query"]] = s
        for s in sorted(seen.values(), key=lambda x: x["score"]):
            emit(f"   {s['symbol']} {s['label']:<5} ({s['score']:>3.0f})  {s['query'][:50]}")
            emit(f"        → ingest source material covering: {s['query']}")
    emit()

    return {"agent": agent, "universal": universal_scores}


# ──────────────────────────────────────────────────────────────────────────
# CROSS-AGENT SECTIONS
# ──────────────────────────────────────────────────────────────────────────

def emit_comparison_matrix(results: list[dict]) -> None:
    emit("=" * 56)
    emit("CROSS-AGENT COMPARISON MATRIX")
    emit("=" * 56)

    agents = [r["agent"] for r in results]
    short = {a: display_name(a).split()[-1][:8] for a in agents}  # surname-ish

    header = f"{'Topic':<34} " + " ".join(f"{short[a]:>9}" for a in agents)
    emit(header)
    emit("─" * 34 + " " + " ".join("─" * 9 for _ in agents))

    by_agent = {r["agent"]: r["universal"] for r in results}
    for t in UNIVERSAL_TOPICS:
        cells = []
        for a in agents:
            s = by_agent[a][t]
            cells.append(f"{s['symbol']}{s['score']:>4.0f}  ")
        emit(f"{t[:34]:<34} " + " ".join(f"{c:>9}" for c in cells))
    emit()


def emit_shared_retrieval(universal_overlap: dict) -> None:
    emit("⚠️  SHARED RETRIEVAL WARNINGS")
    found = False
    for t in UNIVERSAL_TOPICS:
        per_agent = universal_overlap.get(t, {})
        if len(per_agent) < 2:
            continue
        # Count how many agents retrieved each identical chunk text.
        chunk_agents = defaultdict(set)
        for agent, chunks in per_agent.items():
            for c in chunks:
                chunk_agents[c].add(agent)
        max_share = max((len(a) for a in chunk_agents.values()), default=0)
        if max_share >= 2:
            found = True
            emit(f'   ⚠️  SHARED RETRIEVAL: {max_share} agents pulling same chunk on '
                 f'"{t[:34]}" — voices may converge')
    if not found:
        emit("   None — agents draw on distinct passages for the universal topics.")
    emit()


# ──────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Kitchen Table — Knowledge Coverage Audit (MongoDB + Gemini)")
    parser.add_argument("--agent", type=str, default=None,
                        help="Audit a single agent (default: all agents in the registry)")
    parser.add_argument("--output", action="store_true",
                        help="Save the report to outputs/knowledge_audit_<timestamp>.txt")
    args = parser.parse_args()

    # ── Determine target agents ──
    if args.agent:
        requested = [args.agent.lower().replace(" ", "_")]
    else:
        requested = list(AGENT_REGISTRY.keys())

    # Keep only agents that actually have a philosophy collection.
    agents, collections = [], {}
    for a in requested:
        col = load_collection(mongo_philosophy_collection(a))
        if col is None:
            log(f"  WARNING: no philosophy collection for '{a}' — skipping")
            continue
        agents.append(a)
        collections[a] = col

    if not agents:
        log("  ERROR: none of the requested agents have a philosophy collection. Nothing to audit.")
        sys.exit(1)

    # ── Pre-pass: load docs + metadata once per agent; build vocab baseline ──
    # The fingerprint compares each agent against the OTHERS, so we sample every
    # registry agent with a collection (not just the requested ones) for a fair
    # baseline — these are cheap MongoDB reads, no API calls.
    log("  Loading collections and sampling for vocabulary baseline...")
    baseline_agents = list({*agents, *[a for a in AGENT_REGISTRY if load_collection(mongo_philosophy_collection(a))]})
    docs_cache, meta_cache, all_freqs = {}, {}, {}
    for a in baseline_agents:
        col = collections.get(a) or load_collection(mongo_philosophy_collection(a))
        if col is None:
            continue
        collections[a] = col
        # Fetch all docs except the (large) embedding vector — text + metadata only.
        all_docs = list(col.find({}, {"embedding": 0}))
        docs_cache[a] = [d.get("text", "") for d in all_docs if d.get("text")]
        meta_cache[a] = all_docs
        if docs_cache[a]:
            all_freqs[a] = term_frequencies(docs_cache[a])
        log(f"    {display_name(a)}: {len(docs_cache[a]):,} chunks loaded")

    # ── Header ──
    emit("=" * 56)
    emit("KITCHEN TABLE — KNOWLEDGE COVERAGE AUDIT")
    emit("=" * 56)
    emit(f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    emit(f"Agents    : {', '.join(agents)}")
    emit()

    # ── Per-agent audits (sequential — avoid rate limits) ──
    universal_overlap = defaultdict(dict)
    results = []
    for i, a in enumerate(agents, 1):
        log(f"\n  [{i}/{len(agents)}] Auditing {display_name(a)}...")
        results.append(audit_agent(
            a, collections[a], docs_cache.get(a, []), meta_cache.get(a, []),
            all_freqs, universal_overlap,
        ))

    # ── Cross-agent sections (only meaningful for 2+ agents) ──
    if len(results) > 1:
        log("\n  Building cross-agent comparison...")
        emit_comparison_matrix(results)
        emit_shared_retrieval(universal_overlap)

    emit("=" * 56)
    emit("AUDIT COMPLETE")
    emit("=" * 56)

    # ── Optional save ──
    if args.output:
        os.makedirs(OUTPUTS_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(OUTPUTS_DIR, f"knowledge_audit_{ts}.txt")
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(report_lines))
        log(f"\n  Report saved: outputs/knowledge_audit_{ts}.txt")


if __name__ == "__main__":
    main()
