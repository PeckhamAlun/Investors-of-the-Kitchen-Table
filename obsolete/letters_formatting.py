"""
==============================================================================
  BUFFETT LETTERS — AUTO HEADER INSERTER v2
  Splits on "To the Shareholders/Stockholders of Berkshire Hathaway Inc."
==============================================================================

  HOW TO USE:
  1. Make sure your raw file is called: buffett_letters_raw.txt
  2. Run: python add_headers.py
  3. It will find each letter and ask you to type the year for each one
  4. Output saved to: buffett_letters.txt

==============================================================================
"""

import re

INPUT_FILE  = "buffett_letters_raw.txt"
OUTPUT_FILE = "buffett_letters.txt"

# Matches both "Shareholders" and "Stockholders" variants
OPENING_PATTERN = re.compile(
    r"To the Stock(?:holders|holders of Berkshire)|To the Share(?:holders|holders of Berkshire)[^\n]*Berkshire Hathaway[^\n]*",
    re.IGNORECASE
)

def main():
    print("=" * 60)
    print("  Buffett Letters — Auto Header Inserter v2")
    print("=" * 60)

    with open(INPUT_FILE, "r", encoding="utf-8", errors="ignore") as f:
        raw = f.read()

    # Find all opening line positions
    matches = list(OPENING_PATTERN.finditer(raw))

    if not matches:
        print("\n  ERROR: No opening lines found.")
        print("  Make sure your file contains:")
        print("  'To the Shareholders of Berkshire Hathaway Inc.'")
        print("  or")
        print("  'To the Stockholders of Berkshire Hathaway Inc.'")
        return

    print(f"\n  Found {len(matches)} letters.\n")
    print("  I will now ask you for the year of each letter.")
    print("  Just type the 4-digit year and hit Enter.\n")
    print("-" * 60)

    # Split text into letter segments
    segments = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        segments.append((start, end, raw[start:end]))

    output = ""

    for i, (start, end, text) in enumerate(segments):
        # Show a preview of the first 120 chars so user knows which letter
        preview = text[:120].replace("\n", " ").strip()
        print(f"\n  Letter {i + 1} of {len(segments)}")
        print(f"  Preview: {preview}...")
        year = input("  Year: ").strip()

        output += f"\n\n{'='*60}\n"
        output += f"BERKSHIRE HATHAWAY SHAREHOLDER LETTER — {year}\n"
        output += f"{'='*60}\n\n"
        output += text
        print(f"  ✓ Header added for {year}")

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(output)

    print()
    print("=" * 60)
    print(f"  Done. Saved to {OUTPUT_FILE}")
    print(f"  {len(segments)} letters processed")
    print("=" * 60)


if __name__ == "__main__":
    main()