# CLAUDE.md — Kitchen Table

> Master context for this project. Read this first, every session. It tells you
> what the project is, how it's wired, the current live state, and exactly how to
> run, train, diagnose, and extend it.

---

## 1. PROJECT OVERVIEW

**Kitchen Table** is a multi-agent equity-research debate engine. It sits famous
investors — Warren Buffett, Cathie Wood, Peter Lynch (Charlie Munger planned) —
around a virtual table and runs a **structured, multi-round debate** about a
specific stock.

- Each investor is an **agent** with their own **RAG brain**: a ChromaDB
  collection built from that investor's letters, books, lectures, and interview
  transcripts. The agent argues *in their own voice*, grounded in their own
  retrieved philosophy — not a generic LLM persona.
- A separate collection holds the **target company's financial documents**
  (earnings transcripts, 10-Ks/10-Qs, investor decks). Agents cite the company's
  actual numbers.
- Orchestrated with **LangGraph**: agents take turns, see the full session
  history, and a neutral analyst writes a **synthesis** at the end of each round.
- **Output:** a clean, formatted **PDF debate transcript** saved to `outputs/`,
  readable by anyone — designed so a portfolio manager can initiate stock
  coverage quickly from the arguments and the "Go verify" checklists.

**Who it's for:** the author and an investing partner — a decision-support tool
for fast, multi-perspective stock analysis.

---

## 2. TECH STACK

- **Python 3.11 on Windows** — every command uses `py -3.11`.
- **PowerShell terminal** (not CMD — syntax differs; e.g. `$env:VAR`, `;` to
  chain, `.ps1` activation).
- **Virtual environment** at `.venv` — activate before any command:
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```
- **ChromaDB** — local persistent vector store at `./chroma_db/`. No server, no
  network; persists across sessions.
- **Embedding model:** `all-MiniLM-L6-v2` (384-dim, local via
  `sentence-transformers`, free, no API). Cosine space (`hnsw:space: cosine`).
- **LLM:** `claude-sonnet-4-6` via the Anthropic API (`anthropic` SDK).
- **LangGraph** — debate orchestration / multi-agent state.
- **ReportLab** — PDF generation.
- **`ANTHROPIC_API_KEY`** is a **persistent Windows User environment variable**
  set via `setx ANTHROPIC_API_KEY "sk-..."` — **no `.env` file**. (After `setx`,
  open a new terminal for it to take effect.)
- Optional/aux deps: `pdfplumber` (PDF text), `langchain-text-splitters`
  (chunking), `yt-dlp` + `openai-whisper` + `ffmpeg` (YouTube ingest),
  `umap-learn` + `plotly` (DB visualiser).

---

## 3. PROJECT STRUCTURE

```
Kitchen Table/
├── CLAUDE.md                  ← this file
├── config.py                  ← single source of truth: paths, models, AGENT_REGISTRY, helpers
├── main.py                    ← THE DEBATE ENGINE (LangGraph orchestration + PDF output)
├── query.py                   ← single-agent Q&A interface (ask one agent, no debate)
├── youtube_processor.py       ← legacy YouTube helper (referenced by older flows)
├── urls.txt                   ← scratch/root URL list
├── cookies.txt                ← exported YouTube cookies (for yt-dlp bot gate)
├── .gitignore
│
├── agents/                    ← one folder per investor (folder name = agent id)
│   ├── buffett/
│   │   ├── system_prompt.txt          ← Buffett's voice/guardrails (short, RAG-driven)
│   │   ├── buffett_letters_raw.txt    ← raw source (pre-processing)
│   │   └── philosophy/                ← ingested into buffett_philosophy
│   │       ├── buffett_letters.txt
│   │       └── buffett_books_raw/     ← 3 PDFs: Snowball, Tap Dancing to Work, University of Berkshire Hathaway
│   ├── cathie_wood/
│   │   ├── system_prompt.txt          ← (older verbose style — see §14)
│   │   ├── urls.txt                   ← 42 YouTube URLs (source for rebuild)
│   │   └── philosophy/
│   │       └── transcripts/           ← ~21 transcript .txt files + manifest.json (collection currently EMPTY — see §4)
│   └── peter_lynch/
│       ├── system_prompt.txt
│       ├── urls.txt
│       └── philosophy/
│           ├── *.webm                 ← 4 lecture/interview videos (NOT auto-ingested; .webm unsupported)
│           ├── peter_lynch_books_raw/ ← 3 PDFs: One Up On Wall Street, Beating the Street, Learn to Earn
│           └── transcripts/           ← manifest.json
│   (no munger/ folder yet — see §4)
│
├── companies/                 ← raw company documents (drop PDFs here before ingesting)
│   ├── Adobe/adobe_raw/        ← 12 quarterly PDFs (on disk; NOT currently in the DB — see §4)
│   └── Datadog/datadog_raw/    ← 19 transcript/report/deck PDFs (currently loaded in the DB)
│
├── scripts/
│   ├── ingest_philosophy.py   ← build an agent brain from philosophy/ (.txt + .pdf, recursive)
│   ├── ingest_company.py      ← load a company's PDFs into company_financials
│   ├── ingest_youtube.py      ← YouTube → transcript → agent brain (captions, Whisper fallback)
│   ├── pull_transcripts.py    ← older batch: YouTube → Whisper → save .txt to philosophy/ (+ processed_urls.txt)
│   ├── knowledge_audit.py     ← coverage audit across agent philosophy collections
│   ├── audit_rag.py           ← inspect exactly what an agent retrieves for a query
│   └── visualise_db.py        ← UMAP 2D plot of vectors → vector_visualisation.html (buffett-hardcoded)
│
├── chroma_db/                 ← the persistent vector store (do not edit by hand)
├── outputs/                   ← generated debate PDFs (YYYYMMDD_HHMM_session.pdf) + visualiser HTML
├── obsolete/                  ← retired scripts (ingest_buffett.py, ingest_books.py, query_buffett.py, …)
└── _yt_temp/                  ← scratch dir for yt-dlp audio (auto-cleaned)
```

---

## 4. CURRENT STATE (as of 2026-05-31)

**Live ChromaDB collections** (verified):

| Collection | Chunks | Status |
|---|---|---|
| `buffett_philosophy` | 6,429 | ✅ active |
| `peter_lynch_philosophy` | 1,038 | ✅ active |
| `cathie_wood_philosophy` | 0 | ❌ **NEEDS REBUILD** |
| `company_financials` | 1,603 | ✅ **Datadog only** |

- **Active agents:** `buffett` ✅, `peter_lynch` ✅, `cathie_wood` ❌ (0 chunks).
- **Munger:** present in `AGENT_REGISTRY` (config.py) but has **no folder, no
  system prompt, no collection**. Including it in `--agents` will error until built.
- **Company data:** `company_financials` currently holds **Datadog only**
  (1,603 chunks). Adobe's PDFs are on disk (`companies/Adobe/`) but **not in the
  collection** — `ingest_company.py` **wipes on each run by default** (one company
  at a time). Re-ingest Adobe (or use `--append`) to bring it back.
- **Known issue — Cathie rebuild:** her YouTube bulk ingest hit YouTube's bot
  gate (HTTP 429 "confirm you're not a bot"). Fix: export `cookies.txt` and rerun
  the bulk ingest (see §7 and §11). Transcripts already pulled live in
  `agents/cathie_wood/philosophy/transcripts/`.

---

## 5. HOW TO RUN A DEBATE

```powershell
# Specify everything
py -3.11 main.py --topic "Is MongoDB a good investment?" --company mongodb --agents buffett peter_lynch --turns 3

# Choose who speaks first
py -3.11 main.py --topic "..." --company datadog --agents buffett peter_lynch --turns 3 --first peter_lynch

# Show retrieval audit before each agent speaks (see §9)
py -3.11 main.py --topic "..." --company datadog --audit

# Interactive mode (prompts for the topic)
py -3.11 main.py
```

**Flags:** `--topic` (the debate question), `--company` (case-insensitive — see
§8/§10; must already be loaded in the DB), `--agents` (space-separated ids;
default `buffett cathie_wood`), `--turns` (responses per agent per round, default
5), `--first` (reorder so this agent opens), `--audit` (print retrieval report
per agent).

**Multi-round sessions:** after a round finishes (all turns + synthesis), the
engine **prompts for your next topic**. Type another question to run another
round (it carries the full prior history forward), or type `quit` / `stop` /
`exit` / `q` / `done` to finish.

**Output:** on exit, the whole session is written to
`outputs/YYYYMMDD_HHMM_session.pdf` — cover page, each round's turns, agent
bullets/conviction lines, "Go verify" notes, and the analyst synthesis.

---

## 6. HOW TO ADD A NEW AGENT (e.g. Howard Marks — ~5 minutes)

1. **Register** in `config.py` → `AGENT_REGISTRY`:
   ```python
   "howard_marks": {"display": "Howard Marks", "colour": (0.20, 0.30, 0.45)},
   ```
2. **Create the folder:** `agents/howard_marks/`
3. **Add source material:** `agents/howard_marks/philosophy/` — drop in `.txt`
   files and/or a `*_books_raw/` subfolder of PDFs (memos, books, transcripts).
4. **Write the voice:** `agents/howard_marks/system_prompt.txt` — short, voice +
   guardrails only, RAG-driven (see §14; copy Buffett/Lynch as the template).
5. **Build the brain:**
   ```powershell
   py -3.11 scripts/ingest_philosophy.py --agent howard_marks
   ```
6. **Check coverage:**
   ```powershell
   py -3.11 scripts/knowledge_audit.py --agent howard_marks
   ```
7. **Test** with a short debate:
   ```powershell
   py -3.11 main.py --topic "Is Datadog a good investment?" --company datadog --agents buffett howard_marks --turns 2
   ```

That's it — the single `AGENT_REGISTRY` entry wires the agent into the engine,
audits, and PDF colouring automatically.

---

## 7. HOW TO TRAIN AN AGENT

**From PDFs / text files** (rebuilds the collection from `agents/<agent>/philosophy/`):
```powershell
py -3.11 scripts/ingest_philosophy.py --agent buffett
py -3.11 scripts/ingest_philosophy.py --agent buffett --append   # add without wiping
```
Handles `.txt` files and `.pdf` files (including PDFs in subfolders)
automatically. Default run **wipes and rebuilds** that agent's collection;
`--append` adds to it.

**From YouTube** (`ingest_youtube.py`) — needs `cookies.txt` to pass the bot gate:
```powershell
# Single video
py -3.11 scripts/ingest_youtube.py --agent peter_lynch --url "URL" --cookies "C:\Users\peckh\Downloads\cookies.txt"

# Bulk from a urls.txt (one URL per line, '#' for comments)
py -3.11 scripts/ingest_youtube.py --agent cathie_wood --bulk agents/cathie_wood/urls.txt --cookies "C:\Users\peckh\Downloads\cookies.txt"
```
- **Captions-first:** uses free creator captions (`youtube-transcript-api`). For
  caption-less videos, add `--whisper` to enable the audio-download + Whisper
  `large-v3` fallback (needs `ffmpeg` + a JS runtime for YouTube's n-challenge).
- **Duplicate detection is built in** (by URL) — safe to rerun; already-ingested
  videos are skipped, not double-counted.
- Alternative `--cookies-from-browser chrome` exists but is unreliable on Windows
  (locked cookie DB) — prefer an exported `--cookies` file.

**Older Whisper batch tool** (`pull_transcripts.py`): downloads + transcribes
every new URL in `agents/<agent>/urls.txt`, saves `.txt` transcripts into the
philosophy folder, and tracks done URLs in `processed_urls.txt`. After it runs,
re-run `ingest_philosophy.py` to load the new transcripts into ChromaDB.

---

## 8. HOW TO LOAD A COMPANY

`ingest_company.py` takes a **folder of PDFs** and auto-detects the company name
from the folder name (it strips `_raw`/`_data`/etc and Title-cases it).

```powershell
# 1. Put the company's PDFs in companies/<Company>/<company>_raw/
# 2. Point the ingester at that folder:
py -3.11 scripts/ingest_company.py --folder "companies/Datadog/datadog_raw"

# Add a competitor ALONGSIDE the current company (for comparison debates):
py -3.11 scripts/ingest_company.py --folder "companies/Adobe/adobe_raw" --append
```

- **Wipes by default** — each run replaces `company_financials` with the new
  company (the engine debates one company at a time). Use `--append` to keep the
  existing company and add another.
- Company name is stored **Title-cased** (e.g. `Datadog`). The debate engine
  normalizes `--company` to match, so `--company datadog` / `DATADOG` / `Datadog`
  all work (`config.normalize_company`). If the company isn't in the DB, the
  engine refuses to run and lists what *is* loaded.

> **Note:** the `--company <name>` / `--ticker` interface in some examples refers
> to **planned** FMP + SEC EDGAR auto-ingestion (see §12) — **not yet built**.
> Today you drop PDFs in `companies/<Company>/` and use `--folder`.

---

## 9. DIAGNOSTIC TOOLS

**RAG audit** — see exactly which chunks an agent retrieves for a query, split by
philosophy vs company, with source files and health warnings:
```powershell
py -3.11 scripts/audit_rag.py --agent buffett --query "stock based compensation" --company datadog
```

**Knowledge audit** — full coverage report across agents (dynamic taxonomy +
universal benchmark, debate-readiness, source diversity, vocab fingerprint,
cross-agent matrix):
```powershell
py -3.11 scripts/knowledge_audit.py                 # all agents
py -3.11 scripts/knowledge_audit.py --agent buffett # one agent
py -3.11 scripts/knowledge_audit.py --output        # also save outputs/knowledge_audit_<ts>.txt
```
Makes ~2 Claude calls per agent (taxonomy + batched bull/bear framings); degrades
gracefully if a call fails.

**In-debate retrieval audit** — print the per-agent retrieval report *during* a
debate, right before each agent speaks:
```powershell
py -3.11 main.py --topic "..." --company datadog --audit
```
> The flag is **`--audit`** (shares one code path with `audit_rag.py`). There is
> no `--debug` flag.

**Vector visualiser** — UMAP 2D scatter of the vector space → interactive HTML
(currently hardcoded to `buffett_philosophy` + `company_financials`):
```powershell
py -3.11 scripts/visualise_db.py   # writes vector_visualisation.html
```

---

## 10. CHROMADB COLLECTION NAMING CONVENTION

- **Agent philosophy:** `{agent_id}_philosophy` (e.g. `buffett_philosophy`) —
  via `config.philosophy_collection(agent)`.
- **Company data:** `company_financials` — one shared collection, filtered by
  metadata `where={"company": <Title-cased name>}` — via `config.COMPANY_COLLECTION`.
- All naming lives in `config.py`. Don't hardcode collection names elsewhere.

**List every collection and its count:**
```powershell
py -3.11 -c "import chromadb; client = chromadb.PersistentClient(path='./chroma_db'); [print(f'{c.name}: {client.get_collection(c.name).count()}') for c in client.list_collections()]"
```
(Run from the project root so `./chroma_db` resolves.)

---

## 11. KNOWN ISSUES & WORKAROUNDS

- **YouTube bot gate (HTTP 429 "confirm you're not a bot")** → export cookies
  with the **"Get cookies.txt LOCALLY"** Chrome extension and pass via `--cookies
  "C:\path\cookies.txt"`. A `cookies.txt` already exists in the project root.
- **`UnicodeEncodeError` on Windows** (box-drawing/emoji under cp1252) → prefix
  the command with `$env:PYTHONIOENCODING="utf-8";` (audit scripts already try to
  set UTF-8 themselves).
- **API rate limits mid-debate** → `main.py` auto-retries on `RateLimitError`
  (5 attempts, 60s apart) — just let it wait.
- **Munger in `AGENT_REGISTRY` but no collection** → including it in `--agents`
  errors until the agent is built (folder + system_prompt + ingest).
- **`dir /s /b` doesn't work in PowerShell** → use
  `Get-ChildItem -Path agents\ -Recurse | Select-Object FullName`.
- **`.webm` files in a philosophy folder are not ingested** —
  `ingest_philosophy.py` only reads `.txt`/`.pdf`. Transcribe videos first
  (`pull_transcripts.py` / `ingest_youtube.py`).

---

## 12. PLANNED FEATURES BACKLOG (priority order)

1. **Howard Marks agent** — do first.
2. **FMP + SEC EDGAR auto-ingestion** — `ingest_company.py --ticker MDB --years 5`
   (pull filings automatically instead of dropping PDFs by hand).
3. **Knowledge-audit source finder** — yt-dlp + DuckDuckGo (free, no API key) to
   auto-find YouTube videos and articles that fill an agent's coverage gaps.
4. **Auto-audit on ingest** — `ingest_philosophy.py --audit` / `--audit-quick`.
5. **"Go verify" action checklist** aggregated at the end of the PDF.
6. **Auto-math layer** — compute SBC % of revenue, FCF margin, etc. from the
   financial data automatically.
7. **Streamlit UI** for the partner (no CLI required).
8. **Telegram bot + cloud deployment** (mobile access).
9. **Beating the Street for Lynch** — highest-priority training gap. (Note: the
   PDF is already in `peter_lynch/philosophy/peter_lynch_books_raw/` — confirm
   it's ingested, then close this out.)
10. **Cathie Wood YouTube bulk rebuild** — pending the cookie fix.

---

## 13. ARCHITECTURE DECISIONS

- **ChromaDB over Pinecone** — local, free, no network dependency, persists
  across sessions.
- **`all-MiniLM-L6-v2`** — fast, free, local; good enough for financial-text
  similarity.
- **Raw transcripts, not Claude-summarised** — preserves the investor's actual
  voice and specific language for accurate embeddings.
- **Dynamic query expansions over hardcoded** — Claude generates topic-aware
  search expansions at runtime; retrieval quality is dramatically higher.
- **Separate philosophy vs company collections** — keeps persona training
  isolated from company data; prevents cross-contamination.
- **LangGraph for orchestration** — clean multi-agent state; easy to add nodes.
- **Single `AGENT_REGISTRY` in `config.py`** — add an agent once, it works
  everywhere automatically.

---

## 14. SYSTEM PROMPT PHILOSOPHY

All agent system prompts should follow the same pattern:

- **Short** — voice and manner only; no hardcoded investment frameworks.
- **RAG-driven** — all philosophy content is retrieved at runtime, not baked into
  the prompt.
- **Agent-agnostic debate behaviour** — respond to the argument in front of you;
  no hardcoded opponent names.
- **Guardrails** — no biography narration, no generic advice, no referencing past
  funds/holdings; apply the framework to the company in front of you.

`buffett` and `peter_lynch` exemplify this (tight, voice + guardrails, "retrieve
your philosophy" rather than listing it). **`cathie_wood`'s current prompt is the
older, more prescriptive style** — it hardcodes frameworks (Wright's Law, TAM
expansion, 5-year horizon, etc.). Rewrite it to match the lean RAG-driven pattern
when her collection is rebuilt.

---

## 15. SESSION WORKFLOW (typical)

1. **Activate** the venv: `.\.venv\Scripts\Activate.ps1`
2. **Confirm** `ANTHROPIC_API_KEY` is set (`$env:ANTHROPIC_API_KEY` should print a key).
3. **Load company data** if it's a new company:
   `py -3.11 scripts/ingest_company.py --folder "companies/<Company>/<company>_raw"`
4. **Run the debate:**
   `py -3.11 main.py --topic "..." --company <X> --agents ... --turns N`
5. **PDF** saves automatically to `outputs/`.
6. **If an agent seems off** → `py -3.11 scripts/audit_rag.py --agent <a> --query "..." --company <X>` to inspect what it actually retrieved.
7. **If you suspect training gaps** → `py -3.11 scripts/knowledge_audit.py --agent <a>`.
