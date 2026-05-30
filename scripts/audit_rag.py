"""
==============================================================================
  AUDIT_RAG.PY — RAG RETRIEVAL AUDITOR
  Interrogate what an agent actually retrieves from ChromaDB for a given query,
  completely independently of the debate engine.

  Runs the SAME retrieval logic as main.py — same embedding model, same ChromaDB
  path, same collections, same expansion + intent classification — and prints a
  readable report of every chunk pulled, split by collection and source, plus
  warnings when retrieval looks unhealthy. Read-only: writes nothing.

  This is a thin CLI wrapper: the retrieval (main.retrieve_records) and the
  report (main.print_retrieval_report) live in main.py, so this tool and the
  debate engine's --audit flag are guaranteed to be the exact same code.
==============================================================================

  USAGE:
      py -3.11 scripts/audit_rag.py --agent buffett --query "stock based compensation" --company datadog
      py -3.11 scripts/audit_rag.py --agent munger --query "moat durability"

==============================================================================
"""

import os
import sys
import argparse

# Box-drawing characters in the report need UTF-8; force it so output survives
# consoles / pipes that default to a legacy codepage (e.g. Windows cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ──────────────────────────────────────────────────────────────────────────
# IMPORTS — reuse main.py's already-initialised resources and helpers.
# Importing main runs its module-level init (loads the SentenceTransformer,
# opens the Chroma client, creates the anthropic_client that build_expansions
# needs). This guarantees identical embedding model / ChromaDB path / collections.
# ──────────────────────────────────────────────────────────────────────────

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from config import philosophy_collection, normalize_company
import main


def parse_args():
    parser = argparse.ArgumentParser(
        description="Audit what an agent retrieves from ChromaDB for a query.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  py -3.11 scripts/audit_rag.py --agent buffett --query "stock based compensation" --company datadog
  py -3.11 scripts/audit_rag.py --agent munger --query "moat durability"
        """,
    )
    parser.add_argument("--agent",   type=str, required=True, help="Agent id, e.g. buffett")
    parser.add_argument("--query",   type=str, required=True, help="Retrieval query string")
    parser.add_argument("--company", type=str, default=None,  help="Company filter, e.g. datadog")
    return parser.parse_args()


def main_audit():
    args = parse_args()

    agent   = args.agent.lower().replace(" ", "_")
    query   = args.query
    company = normalize_company(args.company) if args.company else None

    # ── Validate philosophy collection exists (mirror main.py) ──
    if main.load_collection(philosophy_collection(agent)) is None:
        print(f"\n  ERROR: No philosophy collection for '{agent}'")
        print(f"  Run: py -3.11 scripts/ingest_philosophy.py --agent {agent}\n")
        sys.exit(1)

    # ── Validate company exists in the database (mirror main.py) ──
    if company:
        companies = main.available_companies()
        if company not in companies:
            print(f"\n  ERROR: Company '{company}' does not exist in the database.")
            if companies:
                print(f"  Available: {', '.join(companies)}")
            else:
                print(f"  The company_financials collection is empty.")
            print(f"  Ingest it first: py -3.11 scripts/ingest_company.py --folder <company>_raw\n")
            sys.exit(1)

    # ── Retrieve + report — identical code path to the debate engine's --audit ──
    intent = main.classify_topic_intent(query)
    records, expansions = main.retrieve_records(query, agent, company, intent)
    main.print_retrieval_report(agent, query, company, intent, records, expansions)


if __name__ == "__main__":
    main_audit()
