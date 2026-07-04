# CLAUDE.md — The Kitchen Table (TIKT)

> Master context. Read this first, every session. It describes the project **as it
> exists today**: a deployed web app (React + FastAPI + MongoDB Atlas), not the
> old local CLI-only tool. Where this doc and the code disagree, the code wins —
> fix this doc.

---

## 1. PROJECT OVERVIEW

**The Kitchen Table (TIKT)** is a small, internal, 3-person equity-research tool. It
runs an **AI debate between five famous-investor agents** about any stock, so the
team can get a fast, multi-perspective read before initiating coverage.

- Pick a ticker → the app pulls that company's financials, then a panel of investor
  agents (Buffett, Wood, Lynch, Marks, Dalio) **debates it in their own voices**,
  each grounded in their own philosophy corpus and citing the company's actual
  numbers. A neutral analyst writes a synthesis.
- Delivered as a **web app**: React front-end (Vercel) → FastAPI backend (Railway)
  → MongoDB Atlas. The debate streams token-by-token over SSE.
- **Who it's for:** three named users (Peckham, Darius, Royden) plus a master admin
  account. Not public.

---

## 2. USERS & ACCESS

Four accounts, defined in `backend/auth.py` (`_USERS`). Each user's **key is both
their password and their bearer token**, read from an env var; an unset key
disables that user (no error).

| Env var | owner | Notes |
|---|---|---|
| `PECKHAM_KEY` | `peckham` | Peckham Alun |
| `DARIUS_KEY`  | `darius`  | Darius |
| `ROYDEN_KEY`  | `royden`  | Royden |
| `MASTER_KEY`  | `master`  | "Kitchen Table" admin — **sees all users' debate history** |

Per-user **debate-history isolation**: new debate documents are stamped with the
caller's `owner`; `GET /debates` and `GET /debates/{ticker}` filter by it. `master`
sees everything; everyone else sees only their own. Legacy pre-isolation debates
have no `owner` field and are invisible to regular users by design.

---

## 3. TECH STACK

- **Frontend:** React + Vite + Tailwind CSS, React Router. Deployed on **Vercel**.
- **Backend:** **FastAPI** (Python 3.11), served by uvicorn. Deployed on **Railway**.
- **Database:** **MongoDB Atlas** — Singapore (`ap-southeast-1`), **Flex** tier,
  database **`kitchen_table`**. Vector search via the `$vectorSearch` aggregation
  stage; every vector collection has an index named **`vector_index`** on the
  `embedding` path (**3072-dim, cosine**).
- **Embeddings:** Gemini **`gemini-embedding-001`** (3072-dim) via `google-genai`
  (`GOOGLE_API_KEY`).
- **AI (debate LLM):** Anthropic **Claude Sonnet** (`claude-sonnet-4-6`) via the
  `anthropic` SDK. Key: **`ANTHROPIC_API_KEY_DEBATE`** (falls back to
  `ANTHROPIC_API_KEY` — see `config.py`).
- **Financial data:** **FMP (Financial Modeling Prep)** — the **primary source for
  all financial data**: statements, earnings-call transcripts, analyst data, peers,
  quotes, profiles, price history, search. Key: `FMP_API_KEY`.
- **SEC filings:** **SEC EDGAR REST API** (keyless) for 10-K / 10-Q text —
  `backend/research_downloader.py` (downloadable ZIP) and `scripts/analyse_company.py`
  (ingest).
- **Orchestration:** LangGraph (debate state machine, in root `main.py`).
- **PDF:** WeasyPrint (HTML/CSS → PDF; needs pango/cairo system libs) + ReportLab.

Local dev is **Windows + PowerShell**, Python 3.11. Virtual environment lives at
`venv/`:
```powershell
.\venv\Scripts\Activate.ps1        # then use `python …`
# or call it directly: .\venv\Scripts\python.exe …
```

---

## 4. REPO STRUCTURE

```
/ (repo root — the WHOLE repo deploys to Railway; see §9)
├── main.py                 ← DEBATE ENGINE: LangGraph graph, SSE streaming
│                             (stream_debate_round), run_round, PDF output, CLI
├── config.py               ← shared config: paths, models, AGENT_REGISTRY,
│                             normalize_company(), env-var loading (load_dotenv)
├── app.py                  ← LEGACY Streamlit UI — broken/superseded (see §11)
├── youtube_processor.py    ← root-level Whisper helper (legacy; needs path fixes)
├── Dockerfile              ← Railway build (python:3.11-slim; see §9)
├── nixpacks.toml           ← leftover from the earlier Nixpacks build (superseded
│                             by Dockerfile; lists the same pango/cairo libs)
│
├── agents/                 ← one folder per investor (folder name = agent id)
│   └── <agent_id>/
│       ├── system_prompt.txt   ← voice + guardrails (see §12 standard)
│       └── philosophy/         ← source corpus (.txt / .pdf) → *_philosophy collection
│
├── scripts/
│   ├── analyse_company.py      ← FMP + SEC EDGAR ingest → company_financials
│   │                             (ingest_by_ticker()); --ticker/--audit/--list/--append
│   ├── ingest_philosophy.py    ← build an agent brain from agents/<id>/philosophy/
│   ├── ingest_youtube.py       ← YouTube → transcript → agent brain (Whisper fallback)
│   ├── audit_rag.py            ← inspect what an agent retrieves for a query
│   ├── knowledge_audit.py      ← coverage audit across agents
│   ├── query.py                ← single-agent Q&A
│   └── split_memo_collection.py← split a memo anthology PDF (Howard Marks build)
│
├── backend/
│   ├── main.py                 ← FastAPI server — ALL API endpoints (§5)
│   ├── auth.py                 ← bearer-token auth, 4 users (§2)
│   ├── research_downloader.py  ← SEC EDGAR + FMP → downloadable research ZIP
│   └── requirements.txt        ← SLIM runtime deps (what actually ships)
│
├── frontend/
│   ├── src/
│   │   ├── pages/              ← Login, Home, Company, Statements, Debate, History
│   │   ├── components/         ← TickerBar (sidebar), MarketTicker, SearchBar,
│   │   │                         RequireAuth (route guard)
│   │   ├── App.jsx             ← routes; RequireAuth wraps the authed routes
│   │   └── lib/api.js          ← apiFetch() — the single fetch wrapper (§5)
│   ├── vercel.json             ← SPA rewrite so React Router deep links work
│   └── .env.local             ← VITE_API_URL for local dev (gitignored)
│
├── outputs/                    ← generated debate PDFs
├── obsolete/                   ← retired scripts (ChromaDB-era tools, etc.)
└── .env                        ← local secrets (gitignored)
```

> The root `requirements.txt` is the heavy **dev** set (chromadb, torch/whisper,
> umap, plotly, yt-dlp) for local ingest/analysis. The container installs only the
> slim `backend/requirements.txt`.

---

## 5. THE API (`backend/main.py`)

All endpoints live in `backend/main.py`. Reads (`/company/*`, `/market-data`,
`/search`) hit **FMP** and use a small in-process TTL cache. Auth-guarded routes
depend on `get_current_user` (bearer token).

- `GET  /health`
- `GET  /market-data`, `GET /search`
- `GET  /company/{ticker}/profile | price-history | metrics | financials | statements`
- `POST /company/{ticker}/upload-document` *(auth)* — user PDF/TXT/MD/DOCX →
  chunked, Gemini-embedded, written into `company_financials` at the company's
  latest ingest version.
- `POST /company/{ticker}/prepare-research` *(auth, SSE)* + `GET .../download-research/{id}`
  — build & serve a research ZIP via `research_downloader.py`.
- `POST /auth/verify` — login (password == token).
- `POST /debate/start` *(auth, SSE)* — the main event. Resolves the company (auto-
  ingests via FMP if missing), then streams the debate token-by-token; stamps
  `owner` and persists each round to the `debates` collection.
- `GET  /debate/{session_id}` — single session (UUID, unguessable; no owner filter).
- `DELETE /debate/{session_id}` *(auth)*.
- `GET  /debates` *(auth)* and `GET /debates/{ticker}` *(auth)* — history lists,
  **owner-filtered** (master sees all).

**Frontend rule:** every API call goes through **`apiFetch()` in
`frontend/src/lib/api.js`**, which prepends `VITE_API_URL` and attaches the bearer
token. Never `fetch()` the backend directly; never hardcode the Railway URL.

---

## 6. ARCHITECTURE NOTES

- **Dynamic engine load.** Root `main.py` (the debate engine) and `backend/main.py`
  are **both named `main.py`**, so the backend loads the engine at runtime via
  `importlib` (`_load_debate_engine()` in `backend/main.py`) under the module name
  `debate_engine`. **The whole repo must be deployed** (not just `backend/`) or the
  engine, `config.py`, `scripts/`, and `agents/` won't be importable and every
  debate fails.
- **Company financials = full context dump, NOT semantic RAG.** For the company,
  the engine pulls **every** chunk at the latest ingest version and concatenates it,
  financials-first, so the numbers are never truncated. The cap is
  **`MAX_CONTEXT_CHARS = 24000`** (`main.py`). Only **philosophy** retrieval uses
  Gemini vector search (`$vectorSearch`) in Atlas, scoped by `{"agent": <id>}`.
- **Auth / isolation.** Per-user bearer tokens from Railway env vars; owner stamped
  on new debate docs; history endpoints filter by owner (master = all). See §2.
- **Streaming.** The engine drives *synchronous* Anthropic streaming; the backend
  bridges it to the async SSE response over a queue, so the event loop isn't blocked
  during a debate.
- **Frontend config.** The backend URL comes from **`VITE_API_URL`** (set in Vercel
  for prod, `frontend/.env.local` for dev). All calls flow through `apiFetch()`.

---

## 7. AGENTS

Five agents are fully built (registry entry + `system_prompt.txt` + philosophy
collection) and can debate:

| id | display |
|---|---|
| `buffett`      | Warren Buffett |
| `cathie_wood`  | Cathie Wood |
| `peter_lynch`  | Peter Lynch |
| `howard_marks` | Howard Marks |
| `ray_dalio`    | Ray Dalio |

The roster is defined once in `config.py` → `AGENT_REGISTRY`; that single entry
wires an agent into the engine, audits, and PDF colouring. To add one, see §12 for
the prompt standard and `scripts/ingest_philosophy.py` for the brain.

> ⚠️ **Charlie Munger and Michael Burry appear in UI copy but have NO philosophy
> corpus and cannot run debates.** `munger` is in `AGENT_REGISTRY` (config.py) and
> in front-end name maps; Burry appears in some page copy. Selecting either would
> fail at retrieval. **Remove or fix that copy** before anyone tries them as agents,
> or build their corpora first.

---

## 8. DATA SOURCING  *(the old doc got this wrong — this is current)*

- **FMP is the PRIMARY source for everything financial** — statements, earnings-call
  transcripts, analyst data, peers, quotes, profiles, price history, search. Used
  both by the backend read endpoints and by `scripts/analyse_company.py` at ingest.
- **yfinance was REMOVED entirely** — do not reference or reintroduce it.
  (Gotcha: the `source_type` tags stored in Mongo are still literally
  `yfinance_financials` / `yfinance_metrics` — **legacy labels only; the data is
  FMP**. Don't be fooled by the tag name.)
- **SEC EDGAR** is used for **10-K / 10-Q filing text only** — ingested by
  `analyse_company.py` and downloaded by `research_downloader.py`.
- **Embeddings: Gemini `gemini-embedding-001` (3072-dim).** Not all-MiniLM-L6-v2,
  not ChromaDB — those were removed months ago. (`config.py` still defines dead
  constants `EMBED_MODEL = "all-MiniLM-L6-v2"`, `CHROMA_DIR`, and a `chroma_db/`
  folder may linger — all inert. Ignore them.)

---

## 9. ENVIRONMENT VARIABLES

**Railway (backend):**
```
MONGODB_URI
MONGODB_DB_NAME=kitchen_table
FMP_API_KEY
GOOGLE_API_KEY
ANTHROPIC_API_KEY_DEBATE
PECKHAM_KEY
DARIUS_KEY
ROYDEN_KEY
MASTER_KEY
```

**Vercel (frontend):**
```
VITE_API_URL=https://investors-of-the-kitchen-table-production.up.railway.app
```

**Local dev:**
- `.env` at repo root (gitignored) — same backend keys. `config.py` calls
  `load_dotenv()` at import, so every script/CLI picks them up.
- `frontend/.env.local` → `VITE_API_URL=http://localhost:8000`.

> Atlas enforces an **IP allowlist** — local scripts that hit Mongo will time out
> unless your current dev IP is allowlisted in the Atlas dashboard.

---

## 10. DEPLOYMENT

- **Railway (backend + engine):** **Root Directory must be blank** so the build
  context is the whole repo (the backend loads root `main.py` via importlib — §6).
  Builds via the **`Dockerfile`** (`python:3.11-slim`; apt-installs pango/cairo/etc.
  for WeasyPrint; installs only `backend/requirements.txt`; `COPY . .`;
  `CMD uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}`). `nixpacks.toml` is
  a superseded leftover from the earlier Nixpacks build — the Dockerfile is
  authoritative.
- **Vercel (frontend):** Root Directory = **`frontend`**, framework = **Vite**.
  `frontend/vercel.json` provides the SPA rewrite so React Router deep links resolve.
- **MongoDB Atlas:** Singapore (`ap-southeast-1`), Flex tier, DB `kitchen_table`.

---

## 11. LOCAL / CLI OPERATIONS

The root engine still runs as a CLI (useful for local testing and data ops). Run
from the repo root with the venv active.

**Run a debate (CLI):**
```powershell
python main.py --topic "Is MongoDB a good investment?" --company mongodb --agents buffett peter_lynch --turns 3
python main.py --audit   # print each agent's retrieval report before it speaks
```
Flags: `--topic`, `--company` (case-insensitive; routed through
`config.normalize_company()`, must already be in the DB), `--agents`, `--turns`,
`--first`, `--audit`.

**Load a company** (FMP + SEC EDGAR → `company_financials`; **needs `FMP_API_KEY`
and `GOOGLE_API_KEY`**):
```powershell
python scripts/analyse_company.py --ticker MDB          # wipes & loads one company
python scripts/analyse_company.py --ticker DDOG --append# add a competitor alongside
python scripts/analyse_company.py --list                # list loaded company keys
python scripts/analyse_company.py --audit MongoDB
```
Wipes `company_financials` by default (one company at a time); `--append` keeps the
existing one. In the web app this same pipeline (`ingest_by_ticker`) auto-runs from
`POST /debate/start` when a company isn't loaded yet.

**Add / train an agent:** register in `config.py` → `AGENT_REGISTRY`, create
`agents/<id>/system_prompt.txt` (see §12), drop corpus into
`agents/<id>/philosophy/`, then:
```powershell
python scripts/ingest_philosophy.py --agent <id>        # --append to add without wiping
python scripts/knowledge_audit.py --agent <id>
```

**Diagnostics:**
```powershell
python scripts/audit_rag.py --agent buffett --query "stock based compensation" --company MongoDB
```

**List every collection + doc count** (run from repo root so `config` loads `.env`):
```powershell
python -c "from pymongo import MongoClient; from config import MONGODB_URI, MONGODB_DB_NAME; db=MongoClient(MONGODB_URI)[MONGODB_DB_NAME]; [print(n, db[n].count_documents({})) for n in db.list_collection_names()]"
```
If box-drawing/emoji output errors on Windows cp1252, prefix with
`$env:PYTHONIOENCODING="utf-8";`.

**Collection naming:** philosophy = `{agent_id}_philosophy`
(`config.philosophy_collection`); company = shared `company_financials`
(`config.COMPANY_COLLECTION`), scoped by `filter: {"company": <normalized name>}`.
Every vector collection needs the `vector_index` (3072-dim cosine) or retrieval
silently returns nothing.

---

## 12. SYSTEM PROMPT STANDARD (for building/auditing an agent)

Every `agents/<id>/system_prompt.txt` is short, **voice + guardrails only, RAG-
driven** — no hardcoded frameworks, numbers, topic stances, or opponent names (all
of that is retrieved at runtime). Read an existing prompt (e.g. `buffett`) as the
worked example. Six sections, same order, ALL-CAPS headers with `━━━` rules:

1. **IDENTITY** — one immersive paragraph (`You are [Name]. Not a simulation… You
   ARE [Name] —`). Disposition, not biography.
2. **HOW YOUR MIND WORKS** *(copy verbatim)* — thinking comes from retrieved
   passages; frameworks shape the read, not a checklist; respond directly then
   advance; always end with a declarative bottom line, never a balanced summary.
3. **HOW YOU HANDLE EVIDENCE** *(copy verbatim)* — RETRIEVED PASSAGES = the lens;
   FINANCIAL DATA = primary evidence; when retrieval is thin, reason forward from
   principle (never "my material doesn't cover this").
4. **HOW YOU SPEAK** *(agent-specific)* — first person; their voice and what they
   reach for first; ends with a `WHAT YOU NEVER SAY OR DO:` list.
5. **GUARDRAILS** — no biography, no past funds/holdings/employers, no framework
   names as a checklist, no fabricated stats, no balanced conclusions, no opponent
   names.
6. **CITATION RULES** *(copy verbatim)* — `[Source: retrieved philosophy]` /
   `[Source: provided financial data]` / reasoned-from-principle (no citation, but
   say so). **Buffett exception:** philosophy citations use
   `[Source: Berkshire Hathaway YEAR Shareholder Letter]`.

**Never put in a prompt:** named frameworks, topic-specific views, opponent names,
or career history beyond the identity line. If you're writing a framework name, a
number, or a stance — stop; that belongs in the collection, not the prompt.

---

## 13. KNOWN ARCHITECTURAL DEBT

For future sessions to be aware of (not urgent, but shapes decisions):

- **Three UI layers were built sequentially: CLI → Streamlit → React.** `app.py`
  (Streamlit) is **broken and superseded** by the React front-end — **retire it to
  `obsolete/`**.
- **The importlib load hack** (`_load_debate_engine`) exists only because both
  files are named `main.py`. Extracting the engine into a `kitchen_table/` core
  package would fix this properly and kill the "deploy the whole repo" requirement.
- **Duplicate streaming/prompt logic** between the CLI path and the web path — the
  same debate logic is expressed twice and can drift.
- **No async Mongo (Motor).** The backend makes synchronous pymongo calls on the
  event loop; under Atlas latency this can stall requests.
- **Transcript embedding is unchunked** — a retrieval-quality fix is pending.
- **Dead constants / labels:** `config.py`'s `EMBED_MODEL`/`CHROMA_DIR`, any
  `chroma_db/` folder, and the `yfinance_*` `source_type` tags in Mongo are all
  vestigial. Don't treat them as live.

---

## 14. RULES FOR AI SESSIONS

- **Never run random/destructive scripts or any `delete_many` / wipe-and-rebuild
  ingest without explicit owner review.** These operate on the shared live Atlas DB.
- **Dry-run before any destructive data operation** — count/`find_one` first, show
  the result, then act.
- **Verify field names with `find_one()` before querying.** Don't assume schema.
- **Never hardcode the Railway URL** — always `VITE_API_URL` (front-end) /
  `apiFetch()`.
- **All front-end API calls go through `apiFetch()`** in `frontend/src/lib/api.js`.
- **`config.py` exports both old and new alias names** (`philosophy_collection` ↔
  `mongo_philosophy_collection`, `COMPANY_COLLECTION` ↔ `MONGO_COMPANY_COLLECTION`).
  Use whichever the file you're editing already uses; don't churn them.
- **Prefer surgical edits over full-file rewrites** unless the file is small or
  already heavily modified.
- **Deploy the whole repo to Railway** (Root Directory blank) — never just
  `backend/` (§6).
