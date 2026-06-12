"""
==============================================================================
  INGEST_PHILOSOPHY.PY — AGENT BRAIN BUILDER (MongoDB Atlas + Gemini)
  Re-ingests agent philosophy collections from their source files into
  MongoDB Atlas using Gemini embeddings (gemini-embedding-001, 3072-dim).

  This is the MongoDB/Gemini counterpart of scripts/ingest_philosophy.py.
  It reads the SAME source files, chunks them IDENTICALLY (same CHUNK_SIZE,
  CHUNK_OVERLAP, same RecursiveCharacterTextSplitter + separators, same
  year-header sectioning and >100-char validity filter), then swaps only the
  embedding model (Gemini instead of all-MiniLM-L6-v2) and the store
  (MongoDB Atlas instead of ChromaDB).
==============================================================================

  USAGE:

  Ingest a single agent:
      py -3.11 scripts/ingest_philosophy.py --agent buffett

  Ingest every registered agent that has a philosophy folder:
      py -3.11 scripts/ingest_philosophy.py --all

  Dry run (chunk + report, but write NOTHING to MongoDB and skip embedding):
      py -3.11 scripts/ingest_philosophy.py --agent buffett --dry-run

==============================================================================
"""

import os
import re
import sys
import time
import argparse
from datetime import datetime, timezone

import pdfplumber
from langchain_text_splitters import RecursiveCharacterTextSplitter

from google import genai
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi

# Import all settings from config
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from config import (
    MONGODB_URI, MONGODB_DB_NAME,
    GEMINI_EMBED_MODEL, GOOGLE_API_KEY,
    CHUNK_SIZE, CHUNK_OVERLAP,
    mongo_philosophy_collection, philosophy_dir, AGENT_REGISTRY,
)

# ==============================================================================
# BATCHING CONSTANTS
# ==============================================================================

EMBED_BATCH_SIZE = 100   # chunks per Gemini embedding call
BATCH_SLEEP_SECS = 0.1   # pause between batches to avoid rate limits

# ==============================================================================
# ARGUMENT PARSING
# ==============================================================================

parser = argparse.ArgumentParser(
    description="Build an agent's philosophy brain in MongoDB Atlas with Gemini embeddings"
)
group = parser.add_mutually_exclusive_group(required=True)
group.add_argument("--agent", help="Agent name e.g. buffett, cathie_wood")
group.add_argument("--all", action="store_true", help="Ingest every registered agent with a philosophy folder")
parser.add_argument("--dry-run", action="store_true", help="Report what would be ingested without writing to MongoDB")
args = parser.parse_args()

DRY_RUN = args.dry_run

# ==============================================================================
# TEXT EXTRACTION  (identical behaviour to ingest_philosophy.py)
# ==============================================================================

def find_all_files(directory):
    """Recursively finds all .txt and .pdf files in a directory."""
    txt_files = []
    pdf_files = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for file in files:
            filepath = os.path.join(root, file)
            if file.endswith(".txt"):
                txt_files.append(filepath)
            elif file.endswith(".pdf"):
                pdf_files.append(filepath)
    return txt_files, pdf_files

def extract_txt(filepath):
    """Reads a plain text file."""
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def extract_pdf(filepath):
    """Extracts prose text from a PDF using pdfplumber."""
    full_text = ""
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if not page_text or len(page_text.strip()) < 30:
                continue
            tables = page.extract_tables()
            if tables:
                for table in tables:
                    for row in table:
                        clean_row = [str(cell).strip() for cell in row if cell]
                        if clean_row:
                            full_text += "  |  ".join(clean_row) + "\n"
                full_text += "\n"
            else:
                full_text += page_text + "\n\n"
    return full_text.strip()

# Year-header sectioning — same pattern as ingest_philosophy.py
HEADER_PATTERN = re.compile(
    r"={60}\n(.+?LETTER\s*[—-]\s*(\d{4}))\n={60}",
    re.MULTILINE
)

def split_by_year_headers(text, agent_name):
    """Splits text on our standard year headers; whole text if none found."""
    headers = list(HEADER_PATTERN.finditer(text))
    if not headers:
        return [(text, f"{agent_name.replace('_', ' ').title()} Philosophy")]
    sections = []
    for i, match in enumerate(headers):
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        section_text = text[start:end].strip()
        sections.append((section_text, match.group(2)))
    return sections

# Splitter — IDENTICAL to ingest_philosophy.py
splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " "]
)

# ==============================================================================
# CHUNK BUILDING
# ==============================================================================

def build_chunks_for_agent(agent_name):
    """
    Returns a list of chunk records for an agent, each:
        {"text": str, "source": filename, "chunk": int}
    The 'chunk' index resets per source file so the _id
    ({agent}_{filename}_{chunk}) is stable and unique per file.
    """
    phil_dir = philosophy_dir(agent_name)
    txt_files, pdf_files = find_all_files(phil_dir)

    records = []
    failed = []

    # --- .txt files ---
    for filepath in txt_files:
        filename = os.path.basename(filepath)
        try:
            text = extract_txt(filepath)
        except Exception as e:
            print(f"   ERROR reading {filename}: {e}")
            failed.append(filename)
            continue

        sections = split_by_year_headers(text, agent_name)
        idx = 0
        for section_text, _label in sections:
            chunks = splitter.split_text(section_text)
            valid = [c for c in chunks if len(c.strip()) > 100]
            for chunk in valid:
                records.append({"text": chunk, "source": filename, "chunk": idx})
                idx += 1
        print(f"   Source: {filename} — {idx:,} chunks")

    # --- .pdf files ---
    for filepath in pdf_files:
        filename = os.path.basename(filepath)
        try:
            text = extract_pdf(filepath)
        except Exception as e:
            print(f"   ERROR reading {filename}: {e}")
            failed.append(filename)
            continue
        if not text:
            print(f"   WARNING: no text extracted from {filename} — skipping")
            failed.append(filename)
            continue

        chunks = splitter.split_text(text)
        valid = [c for c in chunks if len(c.strip()) > 100]
        idx = 0
        for chunk in valid:
            records.append({"text": chunk, "source": filename, "chunk": idx})
            idx += 1
        print(f"   Source: {filename} — {idx:,} chunks")

    return records, failed

# ==============================================================================
# GEMINI EMBEDDING
# ==============================================================================

def embed_texts(client, texts):
    """Embed a list of texts with Gemini, returning a list of vectors."""
    result = client.models.embed_content(
        model=GEMINI_EMBED_MODEL,
        contents=texts,
    )
    return [e.values for e in result.embeddings]

# ==============================================================================
# PER-AGENT INGEST
# ==============================================================================

def ingest_agent(agent_name, gemini_client, db):
    agent_name = agent_name.lower().replace(" ", "_")
    coll_name = mongo_philosophy_collection(agent_name)
    phil_dir = philosophy_dir(agent_name)

    print()
    print(f"📚 Ingesting {coll_name}...")

    if not os.path.isdir(phil_dir):
        print(f"   SKIP: philosophy folder not found — {phil_dir}")
        return

    records, failed = build_chunks_for_agent(agent_name)
    if not records:
        print(f"   No chunks generated — nothing to ingest.")
        return

    total = len(records)
    print(f"   Total chunks: {total:,}")

    # ---- Dry run: report and stop ----
    if DRY_RUN:
        n_batches = (total + EMBED_BATCH_SIZE - 1) // EMBED_BATCH_SIZE
        print(f"   [DRY RUN] Would embed {total:,} chunks in {n_batches} batch(es) of {EMBED_BATCH_SIZE}")
        print(f"   [DRY RUN] Would write to collection: {coll_name}")
        if failed:
            print(f"   [DRY RUN] Failed files: {', '.join(failed)}")
        print(f"   [DRY RUN] No data written.")
        return

    collection = db[coll_name]

    # ---- Embed + insert in batches, skipping duplicates by _id ----
    n_batches = (total + EMBED_BATCH_SIZE - 1) // EMBED_BATCH_SIZE
    added = 0
    skipped = 0

    for b in range(n_batches):
        start = b * EMBED_BATCH_SIZE
        end = min(start + EMBED_BATCH_SIZE, total)
        batch = records[start:end]

        # Determine which records in this batch are new
        pending = []
        for rec in batch:
            _id = f"{agent_name}_{rec['source']}_{rec['chunk']}"
            if collection.find_one({"_id": _id}, {"_id": 1}):
                print(f"   ⏭️ Already ingested — skipping {_id}")
                skipped += 1
            else:
                pending.append((_id, rec))

        if not pending:
            print(f"   Embedding batch {b + 1}/{n_batches}... (all already ingested)")
            time.sleep(BATCH_SLEEP_SECS)
            continue

        print(f"   Embedding batch {b + 1}/{n_batches}...")
        vectors = embed_texts(gemini_client, [rec["text"] for _id, rec in pending])

        now = datetime.now(timezone.utc).isoformat()
        docs = []
        for (_id, rec), vec in zip(pending, vectors):
            docs.append({
                "_id":         _id,
                "text":        rec["text"],
                "embedding":   vec,
                "source":      rec["source"],
                "agent":       agent_name,
                "chunk":       rec["chunk"],
                "ingested_at": now,
            })

        collection.insert_many(docs)
        added += len(docs)

        time.sleep(BATCH_SLEEP_SECS)

    total_docs = collection.count_documents({})
    print(f"✅ Done: {coll_name}")
    print(f"   Chunks added : {added:,}")
    if skipped:
        print(f"   Skipped (dupe): {skipped:,}")
    if failed:
        print(f"   Failed files : {', '.join(failed)}")
    print(f"   Collection   : {coll_name} ({total_docs:,} total docs)")

# ==============================================================================
# MAIN
# ==============================================================================

def main():
    # Resolve target agents
    if args.all:
        targets = list(AGENT_REGISTRY.keys())
    else:
        targets = [args.agent.lower().replace(" ", "_")]

    print("=" * 60)
    print("  MongoDB Atlas Ingestion — Gemini Embeddings")
    print("=" * 60)
    print(f"  Embedding model: {GEMINI_EMBED_MODEL}")
    print(f"  Database:        {MONGODB_DB_NAME}")
    print(f"  Targets:         {', '.join(targets)}")
    print(f"  Mode:            {'DRY RUN (no writes)' if DRY_RUN else 'LIVE'}")

    # Gemini client (not needed for dry run, but harmless to skip when missing)
    gemini_client = None
    if not DRY_RUN:
        if not GOOGLE_API_KEY:
            print("\n  ERROR: GOOGLE_API_KEY is not set (.env). Cannot embed.")
            sys.exit(1)
        gemini_client = genai.Client(api_key=GOOGLE_API_KEY)

    # MongoDB connection (not needed for dry run)
    db = None
    mongo_client = None
    if not DRY_RUN:
        if not MONGODB_URI:
            print("\n  ERROR: MONGODB_URI is not set (.env). Cannot connect.")
            sys.exit(1)
        mongo_client = MongoClient(MONGODB_URI, server_api=ServerApi('1'))
        db = mongo_client[MONGODB_DB_NAME]

    for agent_name in targets:
        ingest_agent(agent_name, gemini_client, db)

    if mongo_client is not None:
        mongo_client.close()

    print()
    print("=" * 60)
    print("  INGESTION COMPLETE" + ("  (dry run)" if DRY_RUN else ""))
    print("=" * 60)

if __name__ == "__main__":
    main()
