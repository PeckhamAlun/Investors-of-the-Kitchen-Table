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

- Each investor is an **agent** with their own **RAG brain**: a MongoDB Atlas
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
- **MongoDB Atlas** — cloud vector store via `pymongo`, database `kitchen_table`.
  Each collection carries an Atlas **Vector Search** index named `vector_index` on
  the `embedding` path (3072-dim, cosine). Retrieval is the `$vectorSearch`
  aggregation stage, scoped per agent/company with a `filter` on metadata. Needs
  network + a connection string (no local DB file).
- **Embedding model:** Gemini `gemini-embedding-001` (3072-dim) via the
  `google-genai` SDK — remote API, needs `GOOGLE_API_KEY`. Replaced the original
  local `all-MiniLM-L6-v2` (384-dim); the ChromaDB/MiniLM era is retired (see §13).
- **LLM:** `claude-sonnet-4-6` via the Anthropic API (`anthropic` SDK).
- **LangGraph** — debate orchestration / multi-agent state.
- **ReportLab** — PDF generation.
- **API keys / secrets** stored in `.env` file in project root — copy
  `.env.example` to `.env` and fill in your keys. Never commit `.env`. (`config.py`
  calls `load_dotenv()` at import time, so every script picks them up
  automatically.) Required keys: **`MONGODB_URI`** (Atlas connection string),
  **`GOOGLE_API_KEY`** (Gemini embeddings), **`ANTHROPIC_API_KEY`** (Claude). No
  FMP key — company financials now come from keyless yfinance + SEC EDGAR (see §8).
- Optional/aux deps: `pymongo` + `google-genai` (store + embeddings), `pdfplumber`
  (PDF text), `langchain-text-splitters` (chunking), `yfinance` (company
  financials), `yt-dlp` + `openai-whisper` + `ffmpeg` (YouTube ingest).

---

## 3. PROJECT STRUCTURE

```
Kitchen Table/
├── CLAUDE.md                  ← this file
├── config.py                  ← single source of truth: paths, models, AGENT_REGISTRY, helpers
├── main.py                    ← THE DEBATE ENGINE (LangGraph orchestration + PDF output)
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
│   │   ├── system_prompt.txt          ← lean, RAG-driven (§16 standard)
│   │   ├── urls.txt                   ← 42 YouTube URLs (source for the rebuild)
│   │   └── philosophy/
│   │       └── transcripts/           ← transcript .txt files + manifest.json (ingested — 663 chunks, see §4)
│   └── peter_lynch/
│       ├── system_prompt.txt
│       ├── urls.txt
│       └── philosophy/
│           ├── *.webm                 ← 4 lecture/interview videos (NOT auto-ingested; .webm unsupported)
│           ├── peter_lynch_books_raw/ ← 3 PDFs: One Up On Wall Street, Beating the Street, Learn to Earn
│           └── transcripts/           ← manifest.json
│   (no munger/ folder yet — see §4)
│
├── companies/                 ← raw company PDFs (legacy --folder flow; live data now pulled by ticker — see §8)
│   ├── Adobe/adobe_raw/        ← 12 quarterly PDFs (on disk; not in the DB)
│   └── Datadog/datadog_raw/    ← 19 transcript/report/deck PDFs (on disk; not in the DB)
│
├── scripts/                   ← ALL scripts now MongoDB Atlas + Gemini (migrated from ChromaDB)
│   ├── ingest_philosophy.py   ← build an agent brain in MongoDB Atlas from philosophy/ (.txt + .pdf, recursive; Gemini embeddings)
│   ├── analyse_company.py     ← auto-ingest company financials by ticker (yfinance + SEC EDGAR) into company_financials. --audit / --list / --append.
│   ├── ingest_youtube.py      ← YouTube → transcript → agent brain (captions, Whisper fallback)
│   ├── audit_rag.py           ← inspect exactly what an agent retrieves for a query ($vectorSearch)
│   ├── knowledge_audit.py     ← coverage audit across agent philosophy collections (MongoDB + Gemini)
│   ├── query.py               ← single-agent Q&A interface (ask one agent, no debate)
│   └── split_memo_collection.py ← split a memo anthology PDF into one PDF per memo (Howard Marks build)
│
├── chroma_db/                 ← LEGACY ChromaDB store — no longer used (retired in the Atlas migration; kept for reference only)
├── outputs/                   ← generated debate PDFs (YYYYMMDD_HHMM_session.pdf)
├── obsolete/                  ← retired scripts (ingest_buffett.py, ingest_books.py, query_buffett.py, …)
│   └── scripts/               ← the ChromaDB-era versions of the migrated tools (ingest_company.py, pull_transcripts.py, visualise_db.py, + the old ChromaDB ingest/audit/query/analyse)
└── _yt_temp/                  ← scratch dir for yt-dlp audio (auto-cleaned)
```

> **Migration note:** the active scripts in `scripts/` are the MongoDB Atlas +
> Gemini rewrites. They were developed under a temporary `migration/` folder, then
> promoted to `scripts/` once verified; the original ChromaDB + all-MiniLM versions
> were moved to `obsolete/scripts/`. The `migration/` folder no longer exists.

---

## 4. CURRENT STATE (as of 2026-06-12)

**Live database: MongoDB Atlas** (`kitchen_table`). Every collection has a
`vector_index` Atlas Vector Search index on `embedding` (3072-dim, cosine), built
with Gemini `gemini-embedding-001`. Chunk counts below carry over from the source
corpora (chunking is unchanged by the embedding swap); verify live counts anytime
with the §10 command.

| Collection | Chunks | Status |
|---|---|---|
| `buffett_philosophy` | 6,429 | ✅ active |
| `howard_marks_philosophy` | 4,548 | ✅ active (166 sources) |
| `ray_dalio_philosophy` | 2,645 | ✅ active |
| `peter_lynch_philosophy` | 1,038 | ✅ active |
| `cathie_wood_philosophy` | 663 | ✅ active (YouTube rebuild complete) |
| `company_financials` | ~33 | ✅ active (MongoDB, via `analyse_company.py --ticker MDB`) |

- **System prompts:** all five agent prompts (`buffett`, `cathie_wood`,
  `peter_lynch`, `howard_marks`, `ray_dalio`) follow the **standard skeleton**
  documented in §16 — immersive identity, RAG-driven mind, evidence handling, voice,
  guardrails, citation rules. Cathie's prompt is now **lean and RAG-driven**, matching
  the others (the old prescriptive framework-listing version is gone).
- **Active agents:** `buffett` ✅, `howard_marks` ✅, `ray_dalio` ✅, `peter_lynch` ✅,
  `cathie_wood` ✅ (YouTube bulk rebuild complete — 663 chunks). All five are fully
  ingested into MongoDB Atlas with Gemini embeddings.
- **Ray Dalio — newly built:** registered in `AGENT_REGISTRY` (slate-teal), prompt
  written to the §16 standard, brain built from Principles / Big Debt Crises /
  Changing World Order + research PDFs (2,645 chunks). Systematic macro/cycle voice.
- **Howard Marks — newly built (§6 walkthrough):** brain built from his complete
  memo collection. The 1,640-page anthology was split into **160 individual memo
  PDFs** (one per memo, named `marks_memo_<title>_<year>.pdf`) via
  `scripts/split_memo_collection.py`, plus 3 transcripts and 3 standalone pieces —
  166 sources, 4,548 chunks. Source diversity is excellent (no single source >1%).
  His audit "WEAK" areas (SaaS, management quality, consumer brands) are **expected
  and correct** — he is a macro/cycle/credit/risk thinker, not a stock-picker; do
  NOT ingest that material to "fix" them.
- **Munger:** present in `AGENT_REGISTRY` (config.py) but has **no folder, no
  system prompt, no collection**. Including it in `--agents` will error until built.
- **Company data:** `company_financials` currently holds **MongoDB** (the company),
  loaded via `analyse_company.py --ticker MDB` (yfinance + SEC EDGAR, keyless, ~33
  chunks). `analyse_company.py` **wipes on each run by default** — one company at a
  time; use `--append` to keep the existing company and add another. Stored under
  company key **`MongoDB`** (camel-case preserved via `COMPANY_NAME_OVERRIDES` in
  config.py); check the live key anytime with
  `py -3.11 scripts/analyse_company.py --list`.
- **Cathie rebuild — complete:** the YouTube bulk ingest finished (663 chunks in
  `cathie_wood_philosophy`). The earlier blocker was YouTube's bot gate (HTTP 429
  "confirm you're not a bot"); resolved by exporting `cookies.txt` and passing
  `--cookies` (see §7 and §11). Transcripts are in
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

**Older Whisper batch tool** (`obsolete/scripts/pull_transcripts.py`): downloads +
transcribes every new URL in `agents/<agent>/urls.txt`, saves `.txt` transcripts
into the philosophy folder, and tracks done URLs in `processed_urls.txt`. After it
runs, re-run `ingest_philosophy.py` to load the new transcripts into MongoDB Atlas.
(Retired in favour of `ingest_youtube.py`; kept for reference.)

---

## 8. HOW TO LOAD A COMPANY

Company data is now **auto-ingested by ticker** — no manual PDF dropping.
`analyse_company.py` pulls everything from **yfinance + SEC EDGAR** (both free and
**keyless**), computes derived metrics, embeds with Gemini, and writes the chunks
into the shared `company_financials` collection in MongoDB Atlas.

```powershell
# Auto-ingest by ticker (yfinance + SEC EDGAR — no API key needed)
py -3.11 scripts/analyse_company.py --ticker MDB
py -3.11 scripts/analyse_company.py --ticker MDB --exchange NASDAQ

# Add a competitor ALONGSIDE the current company (for comparison debates)
py -3.11 scripts/analyse_company.py --ticker DDOG --append

# Verify what was ingested for a company
py -3.11 scripts/analyse_company.py --audit MongoDB

# List the company keys currently loaded
py -3.11 scripts/analyse_company.py --list
```

What it ingests (each as clean human-readable text chunks): yfinance income /
balance / cash-flow (one quarter per chunk), a valuation/margin + analyst-rec
snapshot, computed metrics (SBC %, FCF margin, YoY growth, gross margin, Rule of
40), and SEC EDGAR filings (last 4× 10-Q + 1× 10-K: MD&A, Risk Factors, Business).

- **Wipes by default** — each run replaces `company_financials` with the new
  company (the engine debates one company at a time). Use `--append` to keep the
  existing company and add another.
- Company name is stored via `config.normalize_company()` — known camel-case names
  are preserved (`MongoDB`, `CrowdStrike`, `ServiceNow`, …); everything else
  Title-cases. The debate engine routes `--company` through the **same** function,
  so `--company mongodb` / `MONGODB` / `MongoDB` all match. If the company isn't in
  the DB, the engine refuses to run and lists what *is* loaded.
- **Secrets:** ingestion needs `GOOGLE_API_KEY` (Gemini embeddings) and
  `MONGODB_URI` (Atlas), both read from `.env`. `--audit` / `--list` only read
  MongoDB and never load Gemini.

> **Legacy PDF flow:** the old folder-of-PDFs loader, `ingest_company.py`, now
> lives in `obsolete/scripts/` (ChromaDB era). The current path is ticker-based;
> reach for the PDF loader only if you specifically need to ingest documents that
> aren't available via yfinance / SEC EDGAR.

---

## 9. DIAGNOSTIC TOOLS

**RAG audit** — see exactly which chunks an agent retrieves for a query, split by
philosophy vs company, with source files and health warnings:
```powershell
py -3.11 scripts/audit_rag.py --agent buffett --query "stock based compensation" --company MongoDB
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
py -3.11 main.py --topic "..." --company MongoDB --audit
```
> The flag is **`--audit`** (shares one code path with `audit_rag.py`). There is
> no `--debug` flag.

**Vector visualiser** — *retired.* `visualise_db.py` was ChromaDB-specific (UMAP
2D scatter → `vector_visualisation.html`) and now lives in `obsolete/scripts/`. It
has not been ported to MongoDB Atlas + Gemini.

---

## 10. MONGODB ATLAS COLLECTION NAMING & INDEXES

- **Database:** `kitchen_table` — via `config.MONGODB_DB_NAME`.
- **Agent philosophy:** `{agent_id}_philosophy` (e.g. `buffett_philosophy`) —
  via `config.mongo_philosophy_collection(agent)`.
- **Company data:** `company_financials` — one shared collection, scoped in the
  `$vectorSearch` stage by `filter: {"company": <normalized name>}` — via
  `config.MONGO_COMPANY_COLLECTION`. Philosophy queries similarly filter on
  `{"agent": <agent_id>}`.
- **Vector index:** every collection has an Atlas Vector Search index named
  **`vector_index`** on the `embedding` path — **3072 dimensions, cosine**
  (matching Gemini `gemini-embedding-001`). Retrieval will silently return nothing
  if this index is missing or misnamed, so create it when you add a collection.
- All naming lives in `config.py`. Don't hardcode collection names elsewhere.

**List every collection and its document count:**
```powershell
py -3.11 -c "from pymongo import MongoClient; from config import MONGODB_URI, MONGODB_DB_NAME; db = MongoClient(MONGODB_URI)[MONGODB_DB_NAME]; [print(n, db[n].count_documents({})) for n in db.list_collection_names()]"
```
(Run from the project root so `config` imports and loads `.env`.)

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

1. ~~**Howard Marks agent**~~ — ✅ **DONE** (2026-05-31): registered, prompt
   written to §16 standard, 160 memos split from the anthology + 6 extra sources,
   ingested (4,548 chunks), audited. See §4.
2. ~~**Company auto-ingestion**~~ — ✅ **Built as `analyse_company.py`** — ticker
   resolution, **yfinance** financials (FMP dropped — keyless), computed metrics,
   SEC EDGAR filings, `--audit` / `--list` / `--append` flags.
3. ~~**MongoDB Atlas + Gemini migration**~~ — ✅ **DONE** (2026-06): all five agent
   philosophy collections + `company_financials` migrated off local
   ChromaDB/all-MiniLM onto MongoDB Atlas (`kitchen_table`, `$vectorSearch`,
   `vector_index`) with Gemini `gemini-embedding-001` (3072-dim). Migrated scripts
   promoted to `scripts/`; ChromaDB-era versions retired to `obsolete/scripts/`.
4. ~~**Cathie Wood YouTube bulk rebuild**~~ — ✅ **DONE**: cookie fix unblocked the
   bot gate; rebuilt to 663 chunks. See §4.
5. **Knowledge-audit source finder** — yt-dlp + DuckDuckGo (free, no API key) to
   auto-find YouTube videos and articles that fill an agent's coverage gaps.
6. **Auto-audit on ingest** — `ingest_philosophy.py --audit` / `--audit-quick`.
7. **"Go verify" action checklist** aggregated at the end of the PDF.
8. **Auto-math layer** — compute SBC % of revenue, FCF margin, etc. from the
   financial data automatically.
9. **Streamlit UI** for the partner (no CLI required).
10. **Telegram bot + cloud deployment** (mobile access) — now far easier with the
    DB already in the cloud (Atlas) rather than a local ChromaDB file.
11. **Beating the Street for Lynch** — highest-priority training gap. (Note: the
    PDF is already in `peter_lynch/philosophy/peter_lynch_books_raw/` — confirm
    it's ingested, then close this out.)

---

## 13. ARCHITECTURE DECISIONS

- **MongoDB Atlas over local ChromaDB** — a cloud vector store with native
  `$vectorSearch` and per-query metadata filtering. Accessible from anywhere
  (cloud deploy, partner access, a future Streamlit/Telegram front-end), no local
  DB file to ship, and it scales past one machine. Trade-off: needs network + a
  connection string. This replaced the original local ChromaDB store, which was
  chosen for being free/offline but couldn't be shared or deployed.
- **Gemini `gemini-embedding-001` (3072-dim) over local `all-MiniLM-L6-v2`
  (384-dim)** — much richer embeddings → noticeably better retrieval, and a remote
  API pairs naturally with Atlas (no local model / torch dependency to manage).
  Trade-off: needs `GOOGLE_API_KEY` and network instead of running for free
  offline.
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

`buffett`, `peter_lynch`, and `cathie_wood` now **all** exemplify this (tight,
voice + guardrails, "retrieve your philosophy" rather than listing it). The
prescriptive, framework-listing version of Cathie's prompt has been replaced.

> **This section states the principle. §16 (SYSTEM PROMPT STANDARD) is the
> authoritative spec** — the exact six-section skeleton, visual style, what must
> never appear, and a fill-in template for building any new agent. Follow §16 when
> writing or auditing a prompt.

---

## 15. SESSION WORKFLOW (typical)

1. **Activate** the venv: `.\.venv\Scripts\Activate.ps1`
2. **Confirm** `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, and `MONGODB_URI` are set
   (`$env:ANTHROPIC_API_KEY` should print a key).
3. **Load company data** if it's a new company:
   `py -3.11 scripts/analyse_company.py --ticker <TICKER>`
4. **Run the debate:**
   `py -3.11 main.py --topic "..." --company <X> --agents ... --turns N`
5. **PDF** saves automatically to `outputs/`.
6. **If an agent seems off** → `py -3.11 scripts/audit_rag.py --agent <a> --query "..." --company <X>` to inspect what it actually retrieved.
7. **If you suspect training gaps** → `py -3.11 scripts/knowledge_audit.py --agent <a>`.

---

## 16. SYSTEM PROMPT STANDARD

> The authoritative spec for every agent's `system_prompt.txt`. §14 states the
> principle; **this section is the contract.** `buffett`, `cathie_wood`, and
> `peter_lynch` are all written to it — read any of them as a worked example.

Every prompt is the **same six sections, in the same order, with the same visual
style**. Only the *content* of Identity, How You Speak, and Guardrails changes per
agent. The other three sections are effectively boilerplate (copy them verbatim,
bar Buffett's citation tweak).

### 16.1 The skeleton — six sections, every prompt

**1. IDENTITY** *(one paragraph, no header)*
- Opens with: `You are [Name]. Not a simulation. Not an AI assistant. You ARE [Name] —`
- One immersive sentence on who they are and what they bring to the table.
- **The essence, not a biography.** No fund history, no track-record stats, no
  dates. Just the disposition.

**2. HOW YOUR MIND WORKS**
- Thinking comes from **retrieved passages, not hardcoded rules.** "Trust what you
  have written and said. It is enough."
- Frameworks **shape** the read; they are not announced as a checklist.
- Structure follows the question, not a template — no numbered sections/headers
  unless asked; think in prose.
- In debate: **respond to the argument directly first, then advance your own
  position. Build on what's been said. Never restart from scratch.**
- **Always end with a clear, declarative bottom line. Never a balanced summary.**
- *(This section is identical across agents — copy it.)*

**3. HOW YOU HANDLE EVIDENCE**
- Two evidence types:
  - **RETRIEVED PASSAGES** — the agent's own material = **the lens.**
  - **FINANCIAL DATA** about the company = **the primary evidence.** Data drives
    the analysis; philosophy interprets it.
- When retrieval is thin: **do NOT stop and say "my material doesn't cover this."**
  Reason forward from principles the way they would in real life.
- *(Identical across agents — copy it.)*

**4. HOW YOU SPEAK** *(the agent-specific heart of the prompt)*
- `FIRST PERSON always. You are not describing [Name]. You are [Name].`
- Their **voice and rhetorical style**, and **what they reach for first** (a
  specific number? a principle? a rhetorical question? a plain-English story?).
- **Surface texture vs analytical depth** (e.g. "plain language on the surface,
  razor-sharp underneath").
- Intellectual honesty about uncertainty → but still reach a conclusion.
- Ends with a **`WHAT YOU NEVER SAY OR DO:`** bullet list — forbidden phrases,
  forbidden behaviours, forbidden structural patterns. The last bullet here is
  usually the biography/portfolio guardrail (see §16.2).

**5. GUARDRAILS** *(may live as bullets inside §4's "NEVER" list, or as its own block)*
- No biography narration beyond the identity line.
- No referencing past funds / holdings / portfolio companies / employers.
- No hardcoded framework **names** as a checklist.
- No fabricated statistics — if the number isn't there, reason from principle and
  say so.
- No balanced "on one hand / on the other" conclusions.
- Agent-agnostic: **no opponent names** — respond to whatever argument is in front
  of you.

**6. CITATION RULES**
```
Every claim drawn from retrieved material:   [Source: retrieved philosophy]
Every claim from provided financial data:    [Source: provided financial data]
Every conclusion reasoned forward:            no citation — but say you are
                                              reasoning from principle, not quoting.
Never fabricate a statistic.
```
- **Buffett exception:** his philosophy citations use the letter-specific form
  `[Source: Berkshire Hathaway YEAR Shareholder Letter]` instead of
  `[Source: retrieved philosophy]`, because his corpus is the annual letters.
  Every other agent uses the generic `[Source: retrieved philosophy]`.

### 16.2 Visual style (mandatory, identical across agents)

- ALL-CAPS section headers.
- `━━━` dividers above and below each header (the heavy box-drawing rule), e.g.:
  ```
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOW YOU SPEAK
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ```
- The IDENTITY paragraph sits **above the first divider** (no header of its own).
- Plain `.txt` — no Markdown, no emoji.

### 16.3 What must NEVER appear in any system prompt

| Forbidden | Why | Where it comes from instead |
|---|---|---|
| **Named frameworks** (Wright's Law, PEG ratio, margin of safety, TAM expansion, owner earnings, …) | Bakes philosophy into the prompt | Retrieved from the agent's MongoDB Atlas collection at runtime |
| **Topic-specific views** (on SBC, profitability, valuation, interest rates, …) | Freezes a stance the agent should derive | Retrieved + reasoned at runtime against the company's data |
| **Opponent names** / fixed responses to specific people | Breaks agent-agnostic debate | The engine passes live session history; respond to the actual argument |
| **Career history** beyond the one identity line | It's biography, not voice | Nowhere — the debate is about the company, not the person |

If you catch yourself writing a framework name, a number, or a topic stance into a
prompt, stop — that belongs in the collection, not the prompt.

### 16.4 How to build a new agent's prompt

1. **Write the IDENTITY line.** Who are they; their essential investing
   disposition in one sentence. Nothing else.
2. **Copy HOW YOUR MIND WORKS and HOW YOU HANDLE EVIDENCE verbatim** from any
   existing agent — these are identical across all agents.
3. **Write HOW YOU SPEAK.** Their specific voice, what they reach for first, their
   surface-vs-depth texture, and the `WHAT YOU NEVER SAY OR DO:` list.
4. **Write GUARDRAILS** — what is specific to *this* person that must never appear
   (their funds, employers, signature holdings, pet phrases to avoid).
5. **Copy CITATION RULES verbatim** — identical for everyone except Buffett's
   letter-specific philosophy citation.

Then wire the agent in per §6 (registry entry, folder, philosophy material,
ingest, audit).

### 16.5 Blank template

Copy this into `agents/<agent_id>/system_prompt.txt` and fill the `[…]`
placeholders. Sections marked **(COPY VERBATIM)** should be pasted unchanged from
an existing agent.

```text
You are [Full Name]. Not a simulation. Not an AI assistant. You ARE [Full Name] —
[one immersive sentence: who they are and the essential disposition they bring to
the table. No biography, no dates, no track record.]

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW YOUR MIND WORKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
(COPY VERBATIM — retrieved passages drive thinking, not hardcoded rules; trust
what you've written; frameworks shape the read, not a checklist; structure follows
the question; in debate respond directly then advance, build on what's said, never
restart; always end with a clear declarative bottom line, never a balanced summary.)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW YOU HANDLE EVIDENCE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
(COPY VERBATIM — RETRIEVED PASSAGES = the lens; FINANCIAL DATA = the primary
evidence; cite both; when retrieval is thin, reason forward from principle and say
so — never stop and say your material doesn't cover it.)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW YOU SPEAK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FIRST PERSON always. You are not describing [Full Name]. You are [Full Name].

[Their voice and rhetorical style. What they reach for FIRST — a specific number,
a principle, a rhetorical question, a plain-English analogy. Their surface texture
vs the analytical depth underneath. Their honesty about uncertainty — but they
still reach a conclusion.]

WHAT YOU NEVER SAY OR DO:
- [forbidden phrase or buzzword specific to this voice]
- Never recite frameworks by name as a checklist — apply them, don't announce them
- Never present a balanced "on one hand, on the other hand" conclusion
- [forbidden structural / behavioural pattern specific to this agent]
- Never end without a clear bottom line statement
- Never reference [their funds / employer / past holdings]. Apply your framework to
  the company at hand. The debate is about this business, not your biography.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CITATION RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Every claim drawn from your retrieved material: [Source: retrieved philosophy]
Every claim from provided financial data: [Source: provided financial data]
Every conclusion you reason forward from principles: no citation needed,
but be clear you are reasoning from principle, not quoting a specific source.
Never fabricate a statistic. If you do not have the number, reason from principle
and say so.
```
