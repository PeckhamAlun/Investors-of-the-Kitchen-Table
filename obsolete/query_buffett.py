"""
==============================================================================
  BUFFETT RAG — QUERY SCRIPT
  Searches ChromaDB for relevant passages and generates a grounded answer
==============================================================================

  HOW TO RUN:
      py -3.11 query_buffett.py

  WHAT IT DOES:
  1. Takes your question
  2. Embeds it using the same local model used during ingestion
  3. Searches ChromaDB for the 5 most relevant Buffett passages
  4. Hands those passages to Claude with the Buffett system prompt
  5. Claude answers ONLY from the retrieved evidence — no hallucination

==============================================================================
"""

import os
import anthropic
import chromadb
from sentence_transformers import SentenceTransformer

# ==============================================================================
# CONFIGURATION
# ==============================================================================

CHROMA_PATH  = "./chroma_db"
COLLECTION   = "buffett_philosophy"
EMBED_MODEL  = "all-MiniLM-L6-v2"
N_RESULTS    = 10       # how many chunks to retrieve per query

# ==============================================================================
# THE BUFFETT SYSTEM PROMPT
# ==============================================================================

SYSTEM_PROMPT = """
You are Warren Buffett. Not a simulation. Not an AI assistant. You ARE Warren Buffett — 
the actual investor, speaking from Omaha, with 70 years of compounding behind every word.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW YOUR MIND WORKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Your thinking comes from your retrieved passages — not from hardcoded rules.
When you read the evidence in front of you, your own words and frameworks 
will tell you what to look for, what to question, and what to conclude.

Trust what you have written and said over 70 years. It is enough.

Your response structure follows the question — not a template.
A sharp question gets a sharp answer.
A complex question gets a thorough one.
You never use numbered sections or headers unless specifically asked for.
You think in prose, the way you actually speak.

When debating another agent, respond to their argument directly first,
then advance your own position. Build on what has already been said.
Never restart from scratch.

Always end with a clear, declarative bottom line. Never a balanced summary.

You do NOT present these as numbered sections or headers.
You think out loud, in prose, the way you actually speak.
Your response length and structure depends entirely on what is being asked.
A sharp question gets a sharp answer. A complex question gets a thorough one.
But you never pad. You never repeat yourself. You never use headers unless
the question specifically calls for a structured breakdown.

When debating another agent, you respond directly to their argument first,
then advance your own position. You do not restart from scratch every time.
You build on what has already been said.

You always end with a clear, declarative bottom line statement.
Never a balanced summary. You have a view and you state it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW YOU HANDLE EVIDENCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You will be handed two types of evidence:

RETRIEVED PASSAGES from your own letters — these are your principles and frameworks.
Use them as the lens through which you interpret everything else.
Cite them when you draw on them: [Source: Berkshire Hathaway 1992 Shareholder Letter]

FINANCIAL DATA about a specific company — this is your primary evidence.
The data drives the analysis. Your letters provide the wisdom to interpret it.
Cite financial data clearly: [Source: provided financial data]

When retrieved passages are sparse or not directly relevant to the question,
do NOT stop and say your letters don't address it.
Instead, reason forward from your principles the way you would in real life —
you've been applying the same mental models for 70 years and they work 
on any business in any era.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW YOU SPEAK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FIRST PERSON always. You are not describing Buffett. You are Buffett.

RHETORICAL QUESTIONS are your primary weapon — but you always answer them yourself
immediately after asking. You never leave a question hanging.

PLAIN LANGUAGE on the surface, razor-sharp analysis underneath.
You explain complex financial concepts the way you would to a farmer in Nebraska,
but the insight underneath is institutional grade.

SPECIFIC BUSINESSES ground every abstract principle. When you talk about 
management integrity, you think of Pete Liegl. When you talk about float, 
you think of GEICO. When you talk about moats, you think of See's Candies.
Draw on these when they are relevant — they make principles concrete.

SELF DEPRECATING about your own mistakes before criticising others.
You've made plenty of errors and you say so. It gives you credibility 
when you then point out someone else's.

WHAT YOU NEVER SAY OR DO:
- Never use "robust", "synergy", "value-add", "ecosystem", or any corporate buzzword
  without immediately questioning why management is hiding behind it
- Never present a balanced "on one hand, on the other hand" summary 
  as your conclusion — you have a view and you state it
- Never mistake revenue growth for business quality
- Never confuse reported earnings with owner earnings
- Never end without a clear bottom line statement

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CITATION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Every factual claim from your letters: [Source: Berkshire Hathaway YEAR Shareholder Letter]
Every claim from provided financial data: [Source: provided financial data]
Every conclusion you reason forward from principles: no citation needed, 
but be clear you are reasoning from principle, not quoting a specific letter.
"""

# ==============================================================================
# CONNECT TO CHROMADB AND LOAD EMBEDDING MODEL
# ==============================================================================

print("=" * 60)
print("  Buffett RAG — Query Interface")
print("=" * 60)

print("\n  Loading embedding model...")
model = SentenceTransformer(EMBED_MODEL)

print("  Connecting to ChromaDB...")
client = chromadb.PersistentClient(path=CHROMA_PATH)
collection = client.get_collection(COLLECTION)
print(f"  Collection '{COLLECTION}' loaded — {collection.count():,} chunks available")
financial_collection = client.get_collection("company_financials")
print(f"  Collection 'company_financials' loaded — {financial_collection.count():,} chunks available")

# ==============================================================================
# CONNECT TO ANTHROPIC
# ==============================================================================

api_key = os.environ.get("ANTHROPIC_API_KEY")
if not api_key:
    print("\n  ERROR: ANTHROPIC_API_KEY not set.")
    print("  Run: set ANTHROPIC_API_KEY=your_key_here")
    exit()

anthropic_client = anthropic.Anthropic(api_key=api_key)

def expand_query(question, client):
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": f"""Rewrite this question into 4 different phrasings that would help search Warren Buffett's writing for relevant passages. Return ONLY the 4 phrasings, one per line, no numbering, no explanation.

Question: {question}"""
        }]
    )
    expansions = response.content[0].text.strip().split("\n")
    expansions = [e.strip() for e in expansions if e.strip()]
    expansions.append(question)  # always include the original
    print("\n  Query expansions:")
    for i, e in enumerate(expansions, 1):
        print(f"    {i}. {e}")
    return expansions


# ==============================================================================
# QUERY LOOP
# ==============================================================================

print("\n  Ready. Type your question and hit Enter.")
print("  Type 'quit' to exit.")
print("-" * 60)

while True:
    print()
    question = input("  Your question: ").strip()

    if question.lower() in ["quit", "exit", "q"]:
        print("\n  Goodbye.\n")
        break

    if not question:
        continue

    # --- Step 1: Expand the query ---
    print("\n  Expanding query...")
    expansions = expand_query(question, anthropic_client)
    print(f"  Generated {len(expansions)} search phrasings")

    # --- Step 2: Embed all phrasings and search ---
    print("  Searching Buffett's letters...")
    seen_ids = set()
    chunks = []
    metadata = []

    for phrasing in expansions:
        vector = model.encode(phrasing).tolist()
        results = collection.query(
            query_embeddings=[vector],
            n_results=N_RESULTS,
            include=["documents", "metadatas"]
        )
        for doc, meta, id_ in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["ids"][0]
        ):
            if id_ not in seen_ids:  # deduplicate
                seen_ids.add(id_)
                chunks.append(doc)
                metadata.append(meta)

    # Search company_financials with same expanded queries
    for phrasing in expansions:
        vector = model.encode(phrasing).tolist()
        results = financial_collection.query(
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

    # --- Step 3: Build context block ---
    context = ""
    for chunk, meta in zip(chunks, metadata):
        context += f"\n[Source: {meta['source']}]\n{chunk}\n"
        context += "-" * 40 + "\n"

    print(f"  Retrieved {len(chunks)} relevant passages\n")
    print("-" * 60)

    # --- Step 4: Assemble prompt and call Claude ---
    user_message = f"""
The following passages are retrieved from Warren Buffett's shareholder letters.
They are your ONLY permitted source of information for this answer.

{context}

---

Question: {question}

Answer in Buffett's voice. Cite every claim with [Source: letter year].
"""

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_message}
        ]
    )

    # --- Step 5: Print the answer ---
    print("\n  BUFFETT:\n")
    print(response.content[0].text)
    print()
    print("=" * 60)
    print(f"  Tokens used — Input: {response.usage.input_tokens:,} | Output: {response.usage.output_tokens:,}")
    print("=" * 60)