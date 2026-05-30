"""
==============================================================================
  BUFFETT BOOKS — PDF INGESTION SCRIPT
  Processes all PDFs in buffett_books_raw/ and appends to buffett_philosophy
==============================================================================

  HOW TO RUN:
      py -3.11 ingest_books.py

  WHAT IT DOES:
  1. Reads all PDFs from buffett_books_raw/
  2. Extracts clean prose text using pdfplumber
  3. Chunks with the same settings as the letters pipeline
  4. Embeds using the local model
  5. APPENDS to the existing buffett_philosophy collection
     (does NOT delete existing letter chunks)

  BOOKS EXPECTED:
  - Schroeder_The-Snowball-.pdf
  - university-of-berkshire-hathaway.pdf
  - Tap_Dancing_to_Work_-_Carol_J_Loomis....pdf

==============================================================================
"""

import os
import re
import pdfplumber
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
import chromadb

# ==============================================================================
# CONFIGURATION
# ==============================================================================

INPUT_FOLDER  = "./buffett_books_raw"
CHROMA_PATH   = "./chroma_db"
COLLECTION    = "buffett_philosophy"
EMBED_MODEL   = "all-MiniLM-L6-v2"

CHUNK_SIZE    = 1400   # ~350 words
CHUNK_OVERLAP = 200    # ~50 words

# ==============================================================================
# BOOK METADATA — maps filename keywords to clean source labels
# ==============================================================================

BOOK_METADATA = {
    "snowball": {
        "title":  "The Snowball",
        "author": "Alice Schroeder",
        "type":   "Biography"
    },
    "university": {
        "title":  "University of Berkshire Hathaway",
        "author": "Daniel Pecaut",
        "type":   "AGM Notes"
    },
    "tap": {
        "title":  "Tap Dancing to Work",
        "author": "Carol Loomis",
        "type":   "Essays Collection"
    },
    "dancing": {
        "title":  "Tap Dancing to Work",
        "author": "Carol Loomis",
        "type":   "Essays Collection"
    },
    "loomis": {
        "title":  "Tap Dancing to Work",
        "author": "Carol Loomis",
        "type":   "Essays Collection"
    }
}

def get_book_metadata(filename):
    """Match filename to known book metadata."""
    name_lower = filename.lower()
    for keyword, meta in BOOK_METADATA.items():
        if keyword in name_lower:
            return meta
    # Fallback for unknown books
    clean_name = filename.replace(".pdf", "").replace("_", " ").replace("-", " ")
    return {
        "title":  clean_name,
        "author": "Unknown",
        "type":   "Book"
    }

# ==============================================================================
# TEXT EXTRACTION
# ==============================================================================

def extract_text_from_pdf(filepath, title):
    """
    Extracts prose text from PDF.
    Handles tables gracefully — converts them to readable pipe-separated text.
    Skips pages with no extractable text (images, covers, etc.)
    """
    full_text = ""
    total_pages = 0
    skipped_pages = 0

    with pdfplumber.open(filepath) as pdf:
        total_pages = len(pdf.pages)
        print(f"     Total pages: {total_pages}")

        for i, page in enumerate(pdf.pages):
            page_text = page.extract_text()

            if not page_text or len(page_text.strip()) < 50:
                skipped_pages += 1
                continue

            tables = page.extract_tables()

            if tables:
                # Convert tables to readable text
                for table in tables:
                    for row in table:
                        clean_row = [str(cell).strip() for cell in row if cell]
                        if clean_row:
                            full_text += "  |  ".join(clean_row) + "\n"
                full_text += "\n"
            else:
                full_text += page_text + "\n\n"

            # Progress indicator every 100 pages
            if (i + 1) % 100 == 0:
                print(f"     Processed {i + 1}/{total_pages} pages...")

    print(f"     Skipped {skipped_pages} pages (images/covers/blank)")
    return full_text.strip()

# ==============================================================================
# MAIN PIPELINE
# ==============================================================================

print("=" * 60)
print("  Buffett Books — PDF Ingestion Script")
print("=" * 60)

# --- Check folder ---
if not os.path.exists(INPUT_FOLDER):
    print(f"\n  ERROR: Folder '{INPUT_FOLDER}' not found.")
    exit()

pdf_files = [f for f in os.listdir(INPUT_FOLDER) if f.endswith(".pdf")]

if not pdf_files:
    print(f"\n  ERROR: No PDFs found in '{INPUT_FOLDER}/'")
    exit()

print(f"\n  Found {len(pdf_files)} book PDFs:\n")
for f in sorted(pdf_files):
    meta = get_book_metadata(f)
    print(f"    • {f}")
    print(f"      → {meta['title']} by {meta['author']}")

# ==============================================================================
# STEP 1 — EXTRACT AND CHUNK ALL BOOKS
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

print(f"\n  Extracting text from PDFs...\n")

for filename in sorted(pdf_files):
    filepath = os.path.join(INPUT_FOLDER, filename)
    book_meta = get_book_metadata(filename)

    print(f"  ── {book_meta['title']}")

    text = extract_text_from_pdf(filepath, book_meta["title"])

    if not text:
        print(f"     WARNING: No text extracted — skipping\n")
        continue

    print(f"     Characters extracted: {len(text):,}")

    chunks = splitter.split_text(text)
    valid_chunks = [c for c in chunks if len(c.strip()) > 100]

    print(f"     Chunks created: {len(valid_chunks):,}\n")

    # Build source label for citations
    source = f"{book_meta['title']} by {book_meta['author']}"

    for chunk in valid_chunks:
        all_chunks.append(chunk)
        all_metadata.append({
            "source":   source,
            "title":    book_meta["title"],
            "author":   book_meta["author"],
            "type":     book_meta["type"],
            "filename": filename
        })
        all_ids.append(f"book_{book_meta['title'].replace(' ', '_')}_{chunk_index}")
        chunk_index += 1

print(f"  Total chunks across all books: {len(all_chunks):,}")

# ==============================================================================
# STEP 2 — EMBED
# ==============================================================================

print(f"\n  Loading embedding model: {EMBED_MODEL}")
model = SentenceTransformer(EMBED_MODEL)

print(f"\n  Embedding {len(all_chunks):,} chunks...")
print(f"  The Snowball is long — this may take 10-15 minutes. Go make coffee.\n")

embeddings = model.encode(
    all_chunks,
    show_progress_bar=True,
    batch_size=32
)

print(f"\n  Embedding complete. Shape: {embeddings.shape}")

# ==============================================================================
# STEP 3 — APPEND TO EXISTING CHROMADB COLLECTION
# ==============================================================================

print(f"\n  Connecting to ChromaDB at {CHROMA_PATH}...")
client = chromadb.PersistentClient(path=CHROMA_PATH)

# GET existing collection — do NOT delete it
# We are appending book chunks to the existing letter chunks
existing_collections = [c.name for c in client.list_collections()]

if COLLECTION not in existing_collections:
    print(f"  WARNING: '{COLLECTION}' not found — creating fresh collection")
    collection = client.create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine"}
    )
else:
    collection = client.get_collection(COLLECTION)
    existing_count = collection.count()
    print(f"  Existing collection found — {existing_count:,} chunks already stored")
    print(f"  Appending {len(all_chunks):,} new book chunks...")

# Load in batches
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
print(f"  Total chunks now in collection: {collection.count():,}")
print()
print(f"  Books added:")
for f in sorted(pdf_files):
    meta = get_book_metadata(f)
    print(f"    • {meta['title']} by {meta['author']}")
print()
print(f"  Your Buffett RAG brain just got a lot smarter.")
print("=" * 60)