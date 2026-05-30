"""
==============================================================================
  INGEST_PHILOSOPHY.PY — UNIVERSAL AGENT BRAIN BUILDER
  Builds any agent's RAG brain from their philosophy folder.
  Replaces ingest_buffett.py and ingest_books.py entirely.
==============================================================================

  USAGE:

  Build Buffett's brain (wipe and rebuild):
      py -3.11 scripts/ingest_philosophy.py --agent buffett

  Build Cathie Wood's brain:
      py -3.11 scripts/ingest_philosophy.py --agent cathie_wood

  Append new material to an existing brain:
      py -3.11 scripts/ingest_philosophy.py --agent buffett --append

  FOLDER STRUCTURE EXPECTED:
      agents/
        buffett/
          philosophy/
            buffett_letters.txt        ← plain text files ingested directly
            buffett_books_raw/         ← subfolders of PDFs ingested automatically
              Schroeder_The-Snowball-.pdf
              university-of-berkshire-hathaway.pdf

  The script handles both .txt files and .pdf files automatically.
  PDFs inside subfolders are all processed recursively.

==============================================================================
"""

import os
import re
import sys
import argparse
import pdfplumber
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
import chromadb

# Import all settings from config
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
print(f"DEBUG: Looking for config.py in {ROOT}")
from config import (
    CHROMA_DIR, EMBED_MODEL, CHUNK_SIZE, CHUNK_OVERLAP,
    philosophy_collection, philosophy_dir
)
# ==============================================================================
# ARGUMENT PARSING
# ==============================================================================

parser = argparse.ArgumentParser(description="Build any agent's philosophy RAG brain")
parser.add_argument("--agent", required=True, help="Agent name e.g. buffett, cathie_wood")
parser.add_argument("--append", action="store_true", help="Append to existing collection instead of rebuilding")
args = parser.parse_args()

AGENT_NAME   = args.agent.lower().replace(" ", "_")
APPEND_MODE  = args.append
PHIL_DIR     = philosophy_dir(AGENT_NAME)
COLLECTION   = philosophy_collection(AGENT_NAME)

# ==============================================================================
# VALIDATE
# ==============================================================================

print("=" * 60)
print(f"  Philosophy Ingestion — {AGENT_NAME.replace('_', ' ').title()}")
print("=" * 60)

if not os.path.exists(PHIL_DIR):
    print(f"\n  ERROR: Philosophy folder not found:")
    print(f"  {PHIL_DIR}")
    print(f"\n  Create the folder and add source files to it.")
    sys.exit(1)

print(f"\n  Agent:      {AGENT_NAME}")
print(f"  Source:     {PHIL_DIR}")
print(f"  Collection: {COLLECTION}")
print(f"  Mode:       {'APPEND' if APPEND_MODE else 'WIPE AND REBUILD'}")

# ==============================================================================
# FILE DISCOVERY
# Finds all .txt and .pdf files recursively inside philosophy folder
# ==============================================================================

def find_all_files(directory):
    """Recursively finds all .txt and .pdf files in a directory."""
    txt_files = []
    pdf_files = []

    for root, dirs, files in os.walk(directory):
        # Skip hidden folders
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for file in files:
            filepath = os.path.join(root, file)
            if file.endswith(".txt"):
                txt_files.append(filepath)
            elif file.endswith(".pdf"):
                pdf_files.append(filepath)

    return txt_files, pdf_files

txt_files, pdf_files = find_all_files(PHIL_DIR)

print(f"\n  Files found:")
print(f"    .txt files: {len(txt_files)}")
print(f"    .pdf files: {len(pdf_files)}")

if not txt_files and not pdf_files:
    print(f"\n  ERROR: No .txt or .pdf files found in {PHIL_DIR}")
    sys.exit(1)

# ==============================================================================
# TEXT EXTRACTION FUNCTIONS
# ==============================================================================

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

def get_source_label(filepath, agent_name):
    """Generates a clean source label from the filepath."""
    filename = os.path.basename(filepath)
    name = filename.replace(".pdf", "").replace(".txt", "")
    name = name.replace("_", " ").replace("-", " ").strip()
    return name.title()

# ==============================================================================
# PARSE YEAR HEADERS FROM BUFFETT LETTERS FORMAT
# Handles text files with our standard year header format
# ==============================================================================

HEADER_PATTERN = re.compile(
    r"={60}\n(.+?LETTER\s*[—-]\s*(\d{4}))\n={60}",
    re.MULTILINE
)

def split_by_year_headers(text, agent_name):
    """
    If the text file contains our year headers, splits into sections
    and returns list of (text, source_label) tuples.
    If no headers found, returns the whole text as one section.
    """
    headers = list(HEADER_PATTERN.finditer(text))

    if not headers:
        return [(text, f"{agent_name.replace('_', ' ').title()} Philosophy")]

    sections = []
    for i, match in enumerate(headers):
        year = match.group(2)
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        section_text = text[start:end].strip()
        source = f"{agent_name.replace('_', ' ').title()} {year} Shareholder Letter"
        sections.append((section_text, source))

    return sections

# ==============================================================================
# MAIN CHUNKING PIPELINE
# ==============================================================================

splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=["\n\n", "\n", ". ", " "]
)

all_chunks   = []
all_metadata = []
all_ids      = []
chunk_index  = 0
failed_files = []

print(f"\n  Processing files...\n")

# Process .txt files
for filepath in txt_files:
    filename = os.path.basename(filepath)
    print(f"  ── {filename} (text)")

    try:
        text = extract_txt(filepath)
    except Exception as e:
        print(f"     ERROR: {e}")
        failed_files.append(filepath)
        continue

    sections = split_by_year_headers(text, AGENT_NAME)
    print(f"     Sections found: {len(sections)}")

    file_chunks = 0
    for section_text, source_label in sections:
        chunks = splitter.split_text(section_text)
        valid = [c for c in chunks if len(c.strip()) > 100]
        file_chunks += len(valid)

        for chunk in valid:
            all_chunks.append(chunk)
            all_metadata.append({
                "source":     source_label,
                "agent":      AGENT_NAME,
                "file_type":  "text",
                "filename":   filename
            })
            all_ids.append(f"{AGENT_NAME}_txt_{chunk_index}")
            chunk_index += 1

    print(f"     Chunks created: {file_chunks}\n")

# Process .pdf files
for filepath in pdf_files:
    filename = os.path.basename(filepath)
    source_label = get_source_label(filepath, AGENT_NAME)
    print(f"  ── {filename} (PDF)")
    print(f"     Source: {source_label}")

    try:
        text = extract_pdf(filepath)
    except Exception as e:
        print(f"     ERROR: {e}")
        failed_files.append(filepath)
        continue

    if not text:
        print(f"     WARNING: No text extracted — skipping\n")
        failed_files.append(filepath)
        continue

    print(f"     Characters: {len(text):,}")

    chunks = splitter.split_text(text)
    valid = [c for c in chunks if len(c.strip()) > 100]
    print(f"     Chunks: {len(valid):,}\n")

    for chunk in valid:
        all_chunks.append(chunk)
        all_metadata.append({
            "source":    source_label,
            "agent":     AGENT_NAME,
            "file_type": "pdf",
            "filename":  filename
        })
        all_ids.append(f"{AGENT_NAME}_pdf_{chunk_index}")
        chunk_index += 1

print(f"  Total chunks to ingest: {len(all_chunks):,}")

if not all_chunks:
    print("\n  ERROR: No chunks generated.")
    sys.exit(1)

# ==============================================================================
# EMBED
# ==============================================================================

print(f"\n  Loading embedding model: {EMBED_MODEL}")
model = SentenceTransformer(EMBED_MODEL)

print(f"\n  Embedding {len(all_chunks):,} chunks...")
print(f"  Large collections may take 10-15 minutes. Go get a coffee.\n")

embeddings = model.encode(
    all_chunks,
    show_progress_bar=True,
    batch_size=32
)

print(f"\n  Embedding complete.")

# ==============================================================================
# LOAD INTO CHROMADB
# ==============================================================================

print(f"\n  Connecting to ChromaDB...")
client = chromadb.PersistentClient(path=CHROMA_DIR)
existing = [c.name for c in client.list_collections()]

if not APPEND_MODE:
    if COLLECTION in existing:
        print(f"  Wiping existing '{COLLECTION}'...")
        client.delete_collection(COLLECTION)
    collection = client.create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine"}
    )
    print(f"  Fresh collection created.")
else:
    if COLLECTION in existing:
        collection = client.get_collection(COLLECTION)
        print(f"  Appending — {collection.count():,} chunks already stored.")
    else:
        collection = client.create_collection(
            name=COLLECTION,
            metadata={"hnsw:space": "cosine"}
        )

print(f"  Loading into ChromaDB...")

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
print(f"  Agent:        {AGENT_NAME}")
print(f"  Collection:   {COLLECTION}")
print(f"  Chunks added: {len(all_chunks):,}")
print(f"  Total in DB:  {collection.count():,}")
if failed_files:
    print(f"\n  Failed files:")
    for f in failed_files:
        print(f"    • {os.path.basename(f)}")
print()
print(f"  {AGENT_NAME.replace('_', ' ').title()}'s RAG brain is live.")
print("=" * 60)