"""
==============================================================================
  BUFFETT RAG — INGESTION SCRIPT
  Reads buffett_letters.txt, chunks it, embeds it, loads into ChromaDB
==============================================================================

  HOW TO RUN:
      python ingest_buffett.py

  WHAT IT DOES:
  1. Reads your buffett_letters.txt file
  2. Splits it into ~350 word chunks with 50 word overlap
  3. Embeds each chunk using a free local model (no API cost)
  4. Saves everything into a local ChromaDB folder called /chroma_db

  Run this once. After that your database is live forever.
==============================================================================
"""

import os
import re
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
import chromadb

# ==============================================================================
# CONFIGURATION
# ==============================================================================

INPUT_FILE    = "buffett_letters.txt"
CHROMA_PATH   = "./chroma_db"
COLLECTION    = "buffett_philosophy"
EMBED_MODEL   = "all-MiniLM-L6-v2"

CHUNK_SIZE    = 1400   # ~350 words
CHUNK_OVERLAP = 200    # ~50 words

# ==============================================================================
# STEP 1 — READ THE FILE
# ==============================================================================

print("=" * 60)
print("  Buffett RAG — Ingestion Script")
print("=" * 60)

if not os.path.exists(INPUT_FILE):
    print(f"\n  ERROR: {INPUT_FILE} not found.")
    print(f"  Make sure it's in the same folder as this script.")
    exit()

print(f"\n  Reading {INPUT_FILE}...")
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    raw_text = f.read()

print(f"  Total characters: {len(raw_text):,}")

# ==============================================================================
# STEP 2 — EXTRACT YEAR METADATA FROM HEADERS
# ==============================================================================
# We parse the year headers we inserted earlier so each chunk
# knows which letter it came from.

print("\n  Parsing letter headers...")

# Split on our year headers
header_pattern = re.compile(
    r"={60}\nBERKSHIRE HATHAWAY SHAREHOLDER LETTER — (\d{4})\n={60}",
    re.MULTILINE
)

# Find all headers and their positions
headers = list(header_pattern.finditer(raw_text))
print(f"  Found {len(headers)} letters")

# Build a list of (year, text) tuples
letter_sections = []
for i, match in enumerate(headers):
    year = match.group(1)
    start = match.end()
    end = headers[i + 1].start() if i + 1 < len(headers) else len(raw_text)
    text = raw_text[start:end].strip()
    letter_sections.append((year, text))

# ==============================================================================
# STEP 3 — CHUNK THE TEXT
# ==============================================================================

print("\n  Chunking letters...")

splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " "]
)

all_chunks = []      # the text of each chunk
all_metadata = []    # the metadata for each chunk
all_ids = []         # unique ID for each chunk

chunk_index = 0

for year, text in letter_sections:
    chunks = splitter.split_text(text)

    for chunk in chunks:
        # Skip chunks that are too short to be meaningful
        if len(chunk.strip()) < 100:
            continue

        all_chunks.append(chunk)
        all_metadata.append({
            "source": f"Berkshire Hathaway {year} Shareholder Letter",
            "year": int(year),
            "chunk_id": chunk_index
        })
        all_ids.append(f"buffett_{year}_{chunk_index}")
        chunk_index += 1

print(f"  Total chunks created: {len(all_chunks):,}")

# ==============================================================================
# STEP 4 — EMBED THE CHUNKS
# ==============================================================================
# This converts each chunk into 384 numbers.
# Runs locally on your CPU — no API calls, no cost.
# Takes 2-5 minutes for ~1200 chunks.

print(f"\n  Loading embedding model: {EMBED_MODEL}")
print(f"  (Downloads ~80MB on first run, cached after that)")
model = SentenceTransformer(EMBED_MODEL)

print(f"\n  Embedding {len(all_chunks):,} chunks...")
print(f"  This will take 2-5 minutes. Go get a coffee.\n")

embeddings = model.encode(
    all_chunks,
    show_progress_bar=True,
    batch_size=32
)

print(f"\n  Embedding complete. Shape: {embeddings.shape}")

# ==============================================================================
# STEP 5 — LOAD INTO CHROMADB
# ==============================================================================

print(f"\n  Connecting to ChromaDB at {CHROMA_PATH}...")
client = chromadb.PersistentClient(path=CHROMA_PATH)

# Delete collection if it already exists (clean rebuild)
existing = [c.name for c in client.list_collections()]
if COLLECTION in existing:
    print(f"  Existing collection '{COLLECTION}' found — rebuilding...")
    client.delete_collection(COLLECTION)

collection = client.create_collection(
    name=COLLECTION,
    metadata={"hnsw:space": "cosine"}  # cosine similarity for text
)

print(f"  Loading chunks into ChromaDB...")

# Load in batches of 100 to avoid memory issues
BATCH_SIZE = 100
for i in range(0, len(all_chunks), BATCH_SIZE):
    batch_end = min(i + BATCH_SIZE, len(all_chunks))

    collection.add(
        embeddings=embeddings[i:batch_end].tolist(),
        documents=all_chunks[i:batch_end],
        metadatas=all_metadata[i:batch_end],
        ids=all_ids[i:batch_end]
    )

    print(f"  Loaded {batch_end:,} / {len(all_chunks):,} chunks")

# ==============================================================================
# DONE
# ==============================================================================

print()
print("=" * 60)
print(f"  INGESTION COMPLETE")
print(f"  Collection: {COLLECTION}")
print(f"  Total chunks stored: {collection.count():,}")
print(f"  Database location: {CHROMA_PATH}/")
print(f"")
print(f"  Your Buffett RAG brain is live.")
print(f"  Next step: run query_buffett.py to test it.")
print("=" * 60)