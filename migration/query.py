"""
==============================================================================
  QUERY.PY — UNIVERSAL AGENT QUERY INTERFACE (MongoDB Atlas + Gemini)
  Works for any agent. Reads system prompt from agent folder.
  Searches both philosophy and company_financials collections in MongoDB Atlas
  using Gemini embeddings and the same $vectorSearch pipeline as main.py.
==============================================================================

  USAGE:

  Query Buffett:
      py -3.11 migration/query.py --agent buffett

  Query Cathie Wood:
      py -3.11 migration/query.py --agent cathie_wood

  Query any agent:
      py -3.11 migration/query.py --agent <agent_name>

  REQUIREMENTS:
  - Agent must have a philosophy collection in MongoDB Atlas
    (run: py -3.11 migration/ingest_philosophy.py --agent <agent_name>)
  - Agent must have a system_prompt.txt in agents/<agent_name>/
  - company_financials collection must exist for financial queries
    (run: py -3.11 migration/analyse_company.py --ticker <TICKER>)

==============================================================================
"""

import os
import sys
import argparse
import anthropic
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from google import genai as google_genai

# ==============================================================================
# CONFIG IMPORT
# ==============================================================================

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from config import (
    N_RESULTS, CLAUDE_MODEL, MAX_TOKENS,
    MONGODB_URI, MONGODB_DB_NAME,
    GEMINI_EMBED_MODEL, GOOGLE_API_KEY,
    MONGO_COMPANY_COLLECTION, mongo_philosophy_collection,
    system_prompt_path,
)

# ==============================================================================
# ARGUMENT PARSING
# ==============================================================================

parser = argparse.ArgumentParser(description="Query any agent in the debate engine (MongoDB + Gemini)")
parser.add_argument("--agent", required=True, help="Agent name e.g. buffett, cathie_wood")
args = parser.parse_args()

AGENT_NAME   = args.agent.lower().replace(" ", "_")
PHIL_COL     = mongo_philosophy_collection(AGENT_NAME)
PROMPT_PATH  = system_prompt_path(AGENT_NAME)

# ==============================================================================
# LOAD SYSTEM PROMPT
# ==============================================================================

print("=" * 60)
print(f"  {AGENT_NAME.replace('_', ' ').title()} — Query Interface")
print("=" * 60)

if not os.path.exists(PROMPT_PATH):
    print(f"\n  ERROR: System prompt not found at:")
    print(f"  {PROMPT_PATH}")
    print(f"\n  Create a file called system_prompt.txt in:")
    print(f"  agents/{AGENT_NAME}/")
    sys.exit(1)

with open(PROMPT_PATH, "r", encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

print(f"\n  System prompt loaded: {len(SYSTEM_PROMPT):,} characters")

# ==============================================================================
# CONNECT TO MONGODB ATLAS + GEMINI
# ==============================================================================

if not GOOGLE_API_KEY:
    print("\n  ERROR: GOOGLE_API_KEY not set (.env). Cannot embed.")
    sys.exit(1)
if not MONGODB_URI:
    print("\n  ERROR: MONGODB_URI not set (.env). Cannot connect.")
    sys.exit(1)

print(f"  Loading embedding model: {GEMINI_EMBED_MODEL}")
gemini_client = google_genai.Client(api_key=GOOGLE_API_KEY)

print(f"  Connecting to MongoDB Atlas...")
mongo_client = MongoClient(MONGODB_URI, server_api=ServerApi('1'))
db = mongo_client[MONGODB_DB_NAME]
existing = db.list_collection_names()

# Philosophy collection — required
if PHIL_COL not in existing:
    print(f"\n  ERROR: Philosophy collection '{PHIL_COL}' not found.")
    print(f"  Run: py -3.11 migration/ingest_philosophy.py --agent {AGENT_NAME}")
    sys.exit(1)

phil_col = db[PHIL_COL]
print(f"  Philosophy collection: {phil_col.count_documents({}):,} chunks")

# Company financials — optional
financial_col = None
if MONGO_COMPANY_COLLECTION in existing:
    financial_col = db[MONGO_COMPANY_COLLECTION]
    print(f"  Financial collection:  {financial_col.count_documents({}):,} chunks")
else:
    print(f"  Financial collection:  not loaded (philosophy only mode)")

# ==============================================================================
# ANTHROPIC CLIENT
# ==============================================================================

api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    print("\n  ERROR: ANTHROPIC_API_KEY not set.")
    print("  Run: $env:ANTHROPIC_API_KEY='your_key_here'")
    sys.exit(1)

anthropic_client = anthropic.Anthropic(api_key=api_key)

# ==============================================================================
# GEMINI EMBEDDING
# ==============================================================================

def embed(text):
    """Embed a single query string with Gemini, returning a vector (list of floats)."""
    result = gemini_client.models.embed_content(
        model=GEMINI_EMBED_MODEL,
        contents=text,
    )
    return list(result.embeddings[0].values)

# ==============================================================================
# QUERY EXPANSION
# ==============================================================================

def expand_query(question):
    """Rewrites the question into multiple phrasings for better retrieval."""
    response = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": f"""Rewrite this question into 4 different phrasings that would help search an investor's writing for relevant passages. Return ONLY the 4 phrasings, one per line, no numbering, no explanation.

Question: {question}"""
        }]
    )
    expansions = response.content[0].text.strip().split("\n")
    expansions = [e.strip() for e in expansions if e.strip()]
    expansions.append(question)  # always include original
    return expansions

# ==============================================================================
# QUERY LOOP
# ==============================================================================

print(f"\n  Ready. Type your question and hit Enter.")
print(f"  Type 'quit' to exit.")
print("-" * 60)

while True:
    print()
    question = input("  Your question: ").strip()

    if question.lower() in ["quit", "exit", "q"]:
        print(f"\n  Goodbye.\n")
        break

    if not question:
        continue

    # --- Expand query ---
    print(f"\n  Expanding query...")
    expansions = expand_query(question)
    print(f"  Query expansions:")
    for i, e in enumerate(expansions, 1):
        print(f"    {i}. {e}")
    print(f"  Generated {len(expansions)} search phrasings")

    # --- Search both collections ---
    print(f"  Searching {AGENT_NAME} philosophy...")
    seen_ids = set()
    chunks   = []
    metadata = []

    for phrasing in expansions:
        vector = embed(phrasing)

        # Search philosophy — same $vectorSearch pipeline as main.py
        phil_results = phil_col.aggregate([
            {
                "$vectorSearch": {
                    "index": "vector_index",
                    "path": "embedding",
                    "queryVector": vector,
                    "numCandidates": 50,
                    "limit": N_RESULTS,
                    "filter": {"agent": AGENT_NAME}
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
            id_ = str(doc["_id"])
            if id_ not in seen_ids:
                seen_ids.add(id_)
                chunks.append(doc.get("text", ""))
                metadata.append(doc)

        # Search company financials if available
        if financial_col is not None:
            comp_results = financial_col.aggregate([
                {
                    "$vectorSearch": {
                        "index": "vector_index",
                        "path": "embedding",
                        "queryVector": vector,
                        "numCandidates": 50,
                        "limit": N_RESULTS
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
                id_ = str(doc["_id"])
                if id_ not in seen_ids:
                    seen_ids.add(id_)
                    chunks.append(doc.get("text", ""))
                    metadata.append(doc)

    print(f"  Retrieved {len(chunks)} unique passages")
    print("-" * 60)

    # --- Build context block ---
    context = ""
    for chunk, meta in zip(chunks, metadata):
        source = meta.get("source", "Unknown Source")
        context += f"\n[Source: {source}]\n{chunk}\n"
        context += "-" * 40 + "\n"

    # --- Assemble prompt ---
    user_message = f"""The following passages are your only permitted evidence. Use them to ground your response.

{context}

---

Question: {question}
"""

    # --- Call Claude ---
    response = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}]
    )

    # --- Print response ---
    agent_display = AGENT_NAME.replace("_", " ").title()
    print(f"\n  {agent_display.upper()}:\n")
    print(response.content[0].text)
    print()
    print("=" * 60)
    print(f"  Tokens — Input: {response.usage.input_tokens:,} | Output: {response.usage.output_tokens:,}")
    print("=" * 60)
