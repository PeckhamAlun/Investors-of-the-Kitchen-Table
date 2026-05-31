"""
split_memo_collection.py
========================================================================
Split Howard Marks' "complete memo collection.pdf" into individual memo
PDFs, one file per memo.

WHY THIS EXISTS
---------------
The anthology PDF is ~1,640 pages with ~160 memos concatenated back to
back. Ingesting it as a single document gives Marks one giant blob; split
into individual memos, retrieval and the "Go verify" citations become memo-
specific and far more useful.

HOW IT DETECTS MEMOS (validated against the real PDF)
-----------------------------------------------------
Every Marks memo opens with a header block in its first ~6 lines:

    [Memo to: ... / Addendum to ...]      <- optional lead line
    From: Howard [S.] Marks               <- always present
    Re: <Memo Title>                      <- the title

So a page starts a new memo when its first few lines contain BOTH:
  * a "From: Howard ... Marks" line, and
  * a "Re: <title>" line.

The anthology's own intro page ("Re: The Complete Collection") is skipped,
and the cover + table-of-contents pages fall before the first real memo so
they are never emitted.

HOW IT FINDS THE YEAR
---------------------
Body-text date scanning is unreliable (it grabs quoted dates). Instead we
use the copyright footer that ends every memo's first page:

    <YEAR> Oaktree Capital Management, L.P. All Rights Reserved

We take the first such year inside the memo's page range. Falls back to
"undated" if none is found.

OUTPUT
------
agents/howard_marks/philosophy/memos/marks_memo_<title_slug>_<year>.pdf

The original "complete memo collection.pdf" is left untouched.

USAGE
-----
  py -3.11 -m pip install pypdf        # one-time, if needed
  py -3.11 scripts/split_memo_collection.py

This script does NOT run ingest and does NOT delete the source PDF.
========================================================================
"""

import os
import re
import sys

# --- make config importable (project root is one level up from scripts/) ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, ROOT_DIR)

try:
    import pdfplumber
except ImportError:
    sys.exit("ERROR: pdfplumber is not installed.  py -3.11 -m pip install pdfplumber")

try:
    from pypdf import PdfReader, PdfWriter
except ImportError:
    sys.exit("ERROR: pypdf is not installed.  Run:  py -3.11 -m pip install pypdf")

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
PHIL_DIR   = os.path.join(ROOT_DIR, "agents", "howard_marks", "philosophy")
SOURCE_PDF = os.path.join(PHIL_DIR, "complete memo collection.pdf")
OUT_DIR    = os.path.join(PHIL_DIR, "memos")

# ----------------------------------------------------------------------
# Detection patterns
# ----------------------------------------------------------------------
FROM_RE  = re.compile(r"^From:\s*Howard\b.*\bMarks\b", re.IGNORECASE)
RE_RE    = re.compile(r"^Re:\s*(.+)$")
YEAR_RE  = re.compile(r"\b((?:19|20)\d{2})\s+Oaktree\s+Capital\s+Management")
HEADER_SCAN_LINES = 6          # how many leading lines to inspect per page
SKIP_TITLES = {"the complete collection"}   # anthology intro, not a memo


def slugify(title: str) -> str:
    """Title -> safe lowercase filename fragment, capped to a sane length."""
    t = title.lower()
    # drop apostrophes / smart quotes / the U+FFFD replacement char so
    # "can't" -> "cant" rather than "can_t"
    t = re.sub(r"['‘’“”�]", "", t)
    # any remaining run of non-alphanumerics -> single underscore
    t = re.sub(r"[^a-z0-9]+", "_", t).strip("_")
    if len(t) > 50:                       # trim long titles at an underscore
        t = t[:50].rsplit("_", 1)[0]
    return t or "untitled"


def first_nonempty_lines(text: str, n: int) -> list[str]:
    return [ln.strip() for ln in text.split("\n") if ln.strip()][:n]


def main() -> None:
    if not os.path.exists(SOURCE_PDF):
        sys.exit(f"ERROR: source PDF not found:\n  {SOURCE_PDF}")

    os.makedirs(OUT_DIR, exist_ok=True)

    print("=" * 70)
    print("  Howard Marks — memo collection splitter")
    print("=" * 70)
    print(f"  Source: {SOURCE_PDF}")
    print(f"  Output: {OUT_DIR}\n")

    # --- Pass 1: read every page's text, detect memo start pages ----------
    print("  Reading PDF and detecting memo headers ...")
    page_texts: list[str] = []
    starts: list[tuple[int, str]] = []      # (page_index, title)

    with pdfplumber.open(SOURCE_PDF) as pdf:
        total_pages = len(pdf.pages)
        for i, page in enumerate(pdf.pages):
            txt = page.extract_text() or ""
            page_texts.append(txt)

            head = first_nonempty_lines(txt, HEADER_SCAN_LINES)
            has_from = any(FROM_RE.match(ln) for ln in head)
            title = None
            for ln in head:
                m = RE_RE.match(ln)
                if m:
                    title = m.group(1).strip()
                    break

            if has_from and title:
                if title.lower().strip(" .") in SKIP_TITLES:
                    continue
                starts.append((i, title))

    print(f"  Pages scanned:   {total_pages}")
    print(f"  Memos detected:  {len(starts)}\n")

    if not starts:
        sys.exit("ERROR: no memo headers detected — detection pattern may need adjusting.")

    # --- Pass 2: build page ranges, find year, write per-memo PDFs --------
    reader = PdfReader(SOURCE_PDF)
    used_names: dict[str, int] = {}
    summary: list[tuple[str, int, int, str, str]] = []  # title, start, end, year, filename

    for idx, (start_page, title) in enumerate(starts):
        end_page = (starts[idx + 1][0] - 1) if idx + 1 < len(starts) else (total_pages - 1)

        # year: first Oaktree copyright footer inside this memo's range
        year = "undated"
        for p in range(start_page, end_page + 1):
            m = YEAR_RE.search(page_texts[p])
            if m:
                year = m.group(1)
                break

        # filename, de-duplicated if two memos slugify the same
        base = f"marks_memo_{slugify(title)}_{year}"
        if base in used_names:
            used_names[base] += 1
            base = f"{base}_{used_names[base]}"
        else:
            used_names[base] = 0
        filename = base + ".pdf"
        out_path = os.path.join(OUT_DIR, filename)

        # write the page range
        writer = PdfWriter()
        for p in range(start_page, end_page + 1):
            writer.add_page(reader.pages[p])
        with open(out_path, "wb") as fh:
            writer.write(fh)

        summary.append((title, start_page, end_page, year, filename))

    # --- Summary ----------------------------------------------------------
    print("  " + "-" * 66)
    print(f"  {'#':>3}  {'pages':>11}  {'year':<8}  title  ->  file")
    print("  " + "-" * 66)
    for n, (title, sp, ep, year, fname) in enumerate(summary, 1):
        npages = ep - sp + 1
        safe_title = title.encode("ascii", "replace").decode("ascii")
        print(f"  {n:>3}  {sp:>4}-{ep:<4} ({npages:>2})  {year:<8}  {safe_title[:40]}")
        print(f"       -> {fname}")

    print("\n" + "=" * 70)
    print(f"  DONE.  {len(summary)} memos written to:")
    print(f"  {OUT_DIR}")
    undated = sum(1 for s in summary if s[3] == "undated")
    if undated:
        print(f"  NOTE: {undated} memo(s) had no detectable year (named '..._undated.pdf').")
    print()
    print("  The original 'complete memo collection.pdf' was NOT deleted.")
    print("  Verify the list above looks right before doing anything else.")
    print("=" * 70)


if __name__ == "__main__":
    main()
