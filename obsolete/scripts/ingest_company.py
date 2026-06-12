"""
==============================================================================
  UNIVERSAL COMPANY FINANCIAL DATA — INGESTION PIPELINE
  Auto-detects company from folder name. Works for any company, any PDFs.
==============================================================================

  USAGE:

  Load a single company (wipes existing data — clean slate):
      py -3.11 ingest_company.py --folder datadog_raw

  Add a second company alongside existing data (append mode):
      py -3.11 ingest_company.py --folder tesla_raw --append

  FOLDER NAMING:
  Name your folders as: <company>_raw
  Examples: datadog_raw, tesla_raw, disco_raw, apple_raw
  The script extracts the company name automatically from the folder name.

  FILE NAMING (flexible — script auto-detects):
  - "Datadog Q1 2024 Transcript.pdf"
  - "Datadog – Q2 2024 – Transcript – Quartr.pdf"
  - "DDOG Q3 2024 10-Q.pdf"
  - "Annual Report 2023.pdf"
  All formats handled automatically.

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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import normalize_company

# ==============================================================================
# CONFIGURATION
# ==============================================================================

CHROMA_PATH  = "./chroma_db"
COLLECTION   = "company_financials"
EMBED_MODEL  = "all-MiniLM-L6-v2"

CHUNK_SIZE   = 1400
CHUNK_OVERLAP = 200

# ==============================================================================
# ARGUMENT PARSING
# ==============================================================================

parser = argparse.ArgumentParser(description="Ingest company financial PDFs into ChromaDB")
parser.add_argument("--folder", required=True, help="Folder containing PDFs e.g. datadog_raw")
parser.add_argument("--append", action="store_true", help="Append to existing data instead of wiping")
args = parser.parse_args()

INPUT_FOLDER = args.folder.rstrip("/").rstrip("\\")
APPEND_MODE  = args.append

# ==============================================================================
# DETECT COMPANY NAME FROM FOLDER
# ==============================================================================

def extract_company_from_folder(folder_name):
    name = os.path.basename(folder_name)
    for suffix in ["_raw", "_data", "_docs", "_financials", "_pdfs"]:
        name = name.lower().replace(suffix, "")
    return normalize_company(name)

# ==============================================================================
# PARSE METADATA FROM FILENAME
# ==============================================================================

def parse_filename(filename, company_name):
    name = filename.replace(".pdf", "")

    quarter = "Unknown"
    quarter_match = re.search(r"\b(Q[1-4])\b", name, re.IGNORECASE)
    if quarter_match:
        quarter = quarter_match.group(1).upper()
    
    year = "Unknown"
    year_match = re.search(r"\b(20\d{2})\b", name)
    if year_match:
        year = year_match.group(1)
    else:
        # Handle FY23, FY24 format
        fy_match = re.search(r"\bFY(\d{2})\b", name, re.IGNORECASE)
        if fy_match:
            year = "20" + fy_match.group(1)

    name_lower = name.lower()
    if any(x in name_lower for x in ["transcript", "earnings call", "quartr"]):
        doc_type = "Earnings Call Transcript"
    elif any(x in name_lower for x in ["10-k", "10k", "annual report", "annual"]):
        doc_type = "Annual Report"
    elif any(x in name_lower for x in ["10-q", "10q"]):
        doc_type = "Quarterly Report"
    elif any(x in name_lower for x in ["investor day", "conference", "deck", "presentation"]):
        doc_type = "Investor Presentation"
    elif any(x in name_lower for x in ["proxy", "def 14"]):
        doc_type = "Proxy Statement"
    else:
        doc_type = "Financial Document"

    if quarter != "Unknown" and year != "Unknown":
        source = f"{company_name} {quarter} {year} {doc_type}"
    elif year != "Unknown":
        source = f"{company_name} {year} {doc_type}"
    else:
        source = f"{company_name} {doc_type}"

    return {
        "company":  company_name,
        "quarter":  quarter,
        "year":     year,
        "doc_type": doc_type,
        "source":   source,
        "filename": filename
    }

# ==============================================================================
# EXTRACT TEXT FROM PDF
# ==============================================================================

def extract_text_from_pdf(filepath):
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

# ==============================================================================
# MAIN PIPELINE
# ==============================================================================

print("=" * 60)
print("  Universal Company Ingestion Pipeline")
print("=" * 60)

if not os.path.exists(INPUT_FOLDER):
    print(f"\n  ERROR: Folder '{INPUT_FOLDER}' not found.")
    sys.exit(1)

company_name = extract_company_from_folder(INPUT_FOLDER)
print(f"\n  Company detected: {company_name}")
print(f"  Source folder:    {INPUT_FOLDER}/")
print(f"  Mode:             {'APPEND — keeping existing data' if APPEND_MODE else 'WIPE — clean slate'}")

pdf_files = sorted([f for f in os.listdir(INPUT_FOLDER) if f.lower().endswith(".pdf")])

if not pdf_files:
    print(f"\n  ERROR: No PDF files found in '{INPUT_FOLDER}/'")
    sys.exit(1)

print(f"\n  Found {len(pdf_files)} PDF files:\n")
for f in pdf_files:
    meta = parse_filename(f, company_name)
    print(f"    • {meta['source']}")

print()

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

print("  Extracting and chunking PDFs...\n")

for filename in pdf_files:
    filepath = os.path.join(INPUT_FOLDER, filename)
    meta = parse_filename(filename, company_name)
    print(f"  ── {meta['source']}")

    try:
        text = extract_text_from_pdf(filepath)
    except Exception as e:
        print(f"     ERROR: {e}")
        failed_files.append(filename)
        continue

    if not text:
        print(f"     WARNING: No text extracted — skipping")
        failed_files.append(filename)
        continue

    print(f"     Characters: {len(text):,}")
    chunks = splitter.split_text(text)
    valid_chunks = [c for c in chunks if len(c.strip()) > 100]
    print(f"     Chunks: {len(valid_chunks):,}\n")

    for chunk in valid_chunks:
        all_chunks.append(chunk)
        all_metadata.append({
            "source":   meta["source"],
            "company":  meta["company"],
            "quarter":  meta["quarter"],
            "year":     meta["year"],
            "doc_type": meta["doc_type"],
            "filename": filename
        })
        safe_filename = re.sub(r"[^a-zA-Z0-9]", "_", filename)[:50]
        all_ids.append(f"{company_name.lower()}_{safe_filename}_{chunk_index}")
        chunk_index += 1

print(f"  Total chunks to ingest: {len(all_chunks):,}")

if not all_chunks:
    print("\n  ERROR: No chunks generated. Check your PDFs.")
    sys.exit(1)

print(f"\n  Loading embedding model: {EMBED_MODEL}")
model = SentenceTransformer(EMBED_MODEL)

print(f"\n  Embedding {len(all_chunks):,} chunks...\n")
embeddings = model.encode(all_chunks, show_progress_bar=True, batch_size=32)
print(f"\n  Embedding complete.")

print(f"\n  Connecting to ChromaDB...")
client = chromadb.PersistentClient(path=CHROMA_PATH)
existing = [c.name for c in client.list_collections()]

if not APPEND_MODE:
    if COLLECTION in existing:
        print(f"  Wiping existing '{COLLECTION}' collection...")
        client.delete_collection(COLLECTION)
    collection = client.create_collection(name=COLLECTION, metadata={"hnsw:space": "cosine"})
    print(f"  Fresh collection created.")
else:
    if COLLECTION in existing:
        collection = client.get_collection(COLLECTION)
        print(f"  Appending — {collection.count():,} chunks already stored.")
    else:
        collection = client.create_collection(name=COLLECTION, metadata={"hnsw:space": "cosine"})
        print(f"  No existing collection — creating fresh.")

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

print()
print("=" * 60)
print(f"  INGESTION COMPLETE")
print(f"  Company:      {company_name}")
print(f"  Collection:   {COLLECTION}")
print(f"  Chunks added: {len(all_chunks):,}")
print(f"  Total in DB:  {collection.count():,}")
if failed_files:
    print(f"\n  Failed files:")
    for f in failed_files:
        print(f"    • {f}")
print()
print(f"  Next: run query_buffett.py and ask about {company_name}.")
print("=" * 60)