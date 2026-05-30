"""
==============================================================================
  CONFIG.PY — MULTI-AGENT EQUITY DEBATE ENGINE
  Single source of truth for all paths, model settings, and constants.
  Every script imports from here. Change once, updates everywhere.
==============================================================================
"""

import os

# ==============================================================================
# ROOT DIRECTORY
# ==============================================================================

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))

# ==============================================================================
# DIRECTORY PATHS
# ==============================================================================

AGENTS_DIR    = os.path.join(ROOT_DIR, "agents")
COMPANIES_DIR = os.path.join(ROOT_DIR, "companies")
CHROMA_DIR    = os.path.join(ROOT_DIR, "chroma_db")
OUTPUTS_DIR   = os.path.join(ROOT_DIR, "outputs")
SCRIPTS_DIR   = os.path.join(ROOT_DIR, "scripts")

# ==============================================================================
# CHROMADB COLLECTION NAMING CONVENTION
# ==============================================================================

COMPANY_COLLECTION = "company_financials"

def philosophy_collection(agent_name):
    return f"{agent_name.lower().replace(' ', '_')}_philosophy"

def agent_dir(agent_name):
    return os.path.join(AGENTS_DIR, agent_name.lower().replace(" ", "_"))

def philosophy_dir(agent_name):
    return os.path.join(agent_dir(agent_name), "philosophy")

def system_prompt_path(agent_name):
    return os.path.join(agent_dir(agent_name), "system_prompt.txt")

def company_dir(company_name):
    return os.path.join(COMPANIES_DIR, company_name.lower().replace(" ", "_"))

def normalize_company(name):
    """
    Canonical company key — used for BOTH ingestion (the stored metadata) and
    retrieval (the where-filter). Routing every write and read through this one
    function guarantees the stored name and the query filter can never diverge,
    so a --company arg in any casing (datadog, DATADOG, DataDog) always matches
    the ingested data. Returns None/empty unchanged.
    """
    return name.strip().title() if name else name

# ==============================================================================
# EMBEDDING MODEL
# ==============================================================================

EMBED_MODEL = "all-MiniLM-L6-v2"

# ==============================================================================
# CHUNKING SETTINGS
# ==============================================================================

CHUNK_SIZE    = 1400
CHUNK_OVERLAP = 200

# ==============================================================================
# WHISPER (TIER-2 TRANSCRIPTION) SETTINGS
# Used by scripts/ingest_youtube.py when a video has no captions.
# ==============================================================================

WHISPER_MODEL    = "large-v3"
WHISPER_LANGUAGE = "en"   # force language — auto-detect misfires (e.g. Norwegian)
                          # on music/silent intros. Set to "auto" to auto-detect.

# ==============================================================================
# RETRIEVAL SETTINGS
# ==============================================================================

N_RESULTS = 7

# ==============================================================================
# CLAUDE API SETTINGS
# ==============================================================================

CLAUDE_MODEL    = "claude-sonnet-4-6"
MAX_TOKENS      = 3000
EXPANSION_COUNT = 4

# ==============================================================================
# AGENT REGISTRY
# Add a new agent here and it automatically works everywhere.
#
# Each entry:
#   "agent_id": {
#       "display": "Full Name",
#       "colour":  (R, G, B)  — float values 0.0–1.0 for PDF accent colour
#   }
#
# To add a new agent:
#   1. Add an entry below
#   2. Create agents/<agent_id>/system_prompt.txt
#   3. Add philosophy files to agents/<agent_id>/philosophy/
#   4. Run: py -3.11 scripts/ingest_philosophy.py --agent <agent_id>
# ==============================================================================

AGENT_REGISTRY = {
    "buffett": {
        "display": "Warren Buffett",
        "colour":  (0.56, 0.27, 0.07),   # Berkshire brown
    },
    "cathie_wood": {
        "display": "Cathie Wood",
        "colour":  (0.05, 0.35, 0.65),   # ARK blue
    },
    "peter_lynch": {
        "display": "Peter Lynch",
        "colour":  (0.13, 0.45, 0.20),   # Fidelity green
    },
    "munger": {
        "display": "Charlie Munger",
        "colour":  (0.35, 0.10, 0.45),   # purple
    },
}

# Derived lookups — used directly in main.py
AGENT_DISPLAY = {k: v["display"] for k, v in AGENT_REGISTRY.items()}
AGENT_COLOURS = {k: v["colour"]  for k, v in AGENT_REGISTRY.items()}

# ==============================================================================
# QUICK SANITY CHECK
# ==============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  Config — Path Verification")
    print("=" * 60)
    print(f"\n  ROOT:      {ROOT_DIR}")
    print(f"  AGENTS:    {AGENTS_DIR}")
    print(f"  COMPANIES: {COMPANIES_DIR}")
    print(f"  CHROMA:    {CHROMA_DIR}")
    print(f"  OUTPUTS:   {OUTPUTS_DIR}")
    print()
    print("  Helper function examples:")
    print(f"  buffett collection:     {philosophy_collection('buffett')}")
    print(f"  cathie collection:      {philosophy_collection('cathie_wood')}")
    print(f"  buffett philosophy dir: {philosophy_dir('buffett')}")
    print(f"  datadog company dir:    {company_dir('datadog')}")
    print()
    print("  Settings:")
    print(f"  Embed model:   {EMBED_MODEL}")
    print(f"  Chunk size:    {CHUNK_SIZE}")
    print(f"  Chunk overlap: {CHUNK_OVERLAP}")
    print(f"  N results:     {N_RESULTS}")
    print(f"  Claude model:  {CLAUDE_MODEL}")
    print(f"  Max tokens:    {MAX_TOKENS}")
    print()
    print("  Agent registry:")
    for agent_id, info in AGENT_REGISTRY.items():
        print(f"  {agent_id:<15} {info['display']:<20} colour: {info['colour']}")
    print()

    dirs = {
        "agents":    AGENTS_DIR,
        "companies": COMPANIES_DIR,
        "chroma_db": CHROMA_DIR,
        "outputs":   OUTPUTS_DIR,
    }
    print("  Directory check:")
    for name, path in dirs.items():
        exists = "✓ exists" if os.path.exists(path) else "✗ missing"
        print(f"  {name}: {exists}")
    print()
    print("=" * 60)