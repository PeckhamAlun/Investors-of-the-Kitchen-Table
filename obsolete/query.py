"""
==============================================================================
  QUERY.PY — UNIVERSAL AGENT QUERY INTERFACE
  Works for any agent. Reads system prompt from agent folder.
  Searches both philosophy and company_financials collections.
==============================================================================

  USAGE:

  Query Buffett:
      py -3.11 query.py --agent buffett

  Query Cathie Wood:
      py -3.11 query.py --agent cathie_wood

  Query any agent:
      py -3.11 query.py --agent <agent_name>

  REQUIREMENTS:
  - Agent must have a philosophy collection in ChromaDB
    (run: py -3.11 scripts/ingest_philosophy.py --agent <agent_name>)
  - Agent must have a system_prompt.txt in agents/<agent_name>/
  - company_financials collection must exist for financial queries
    (run: py -3.11 scripts/ingest_company.py --folder <company>)

==============================================================================
"""

import os
import sys
import argparse
import anthropic
import chromadb
from sentence_transformers import SentenceTransformer

# ==============================================================================
# CONFIG IMPORT
# ==============================================================================

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    CHROMA_DIR, EMBED_MODEL, N_RESULTS,
    CLAUDE_MODEL, MAX_TOKENS,
    COMPANY_COLLECTION, philosophy_collection,
    system_prompt_path, agent_dir
)

# ==============================================================================
# ARGUMENT PARSING
# ==============================================================================

parser = argparse.ArgumentParser(description="Query any agent in the debate engine")
parser.add_argument("--agent", required=True, help="Agent name e.g. buffett, cathie_wood")
args = parser.parse_args()

AGENT_NAME   = args.agent.lower().replace(" ", "_")
PHIL_COL     = philosophy_collection(AGENT_NAME)
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
# CONNECT TO CHROMADB
# ==============================================================================

print(f"  Loading embedding model...")
model = SentenceTransformer(EMBED_MODEL)

print(f"  Connecting to ChromaDB...")
client = chromadb.PersistentClient(path=CHROMA_DIR)
existing = [c.name for c in client.list_collections()]

# Philosophy collection — required
if PHIL_COL not in existing:
    print(f"\n  ERROR: Philosophy collection '{PHIL_COL}' not found.")
    print(f"  Run: py -3.11 scripts/ingest_philosophy.py --agent {AGENT_NAME}")
    sys.exit(1)

phil_col = client.get_collection(PHIL_COL)
print(f"  Philosophy collection: {phil_col.count():,} chunks")

# Company financials — optional
financial_col = None
if COMPANY_COLLECTION in existing:
    financial_col = client.get_collection(COMPANY_COLLECTION)
    print(f"  Financial collection:  {financial_col.count():,} chunks")
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
        vector = model.encode(phrasing).tolist()

        # Search philosophy
        results = phil_col.query(
            query_embeddings=[vector],
            n_results=N_RESULTS,
            include=["documents", "metadatas"]
        )
        for doc, meta, id_ in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["ids"][0]
        ):
            if id_ not in seen_ids:
                seen_ids.add(id_)
                chunks.append(doc)
                metadata.append(meta)

        # Search company financials if available
        if financial_col:
            results = financial_col.query(
                query_embeddings=[vector],
                n_results=N_RESULTS,
                include=["documents", "metadatas"]
            )
            for doc, meta, id_ in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["ids"][0]
            ):
                if id_ not in seen_ids:
                    seen_ids.add(id_)
                    chunks.append(doc)
                    metadata.append(meta)

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