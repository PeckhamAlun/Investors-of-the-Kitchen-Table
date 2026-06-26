import { Fragment, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";

// Debate.jsx — /debate/:ticker. Two phases:
//   PHASE 1 (status idle | ingesting): a full-width setup screen where the user
//     picks investors, the topic, and turns-per-agent, then starts the debate.
//     While the backend ingests research data, an inline progress card appears
//     below the (still-visible) form.
//   PHASE 2 (status running | complete | error): the live transcript — a 70%
//     transcript panel + 30% session sidebar — streamed token-by-token over SSE.
// Editorial TIKT look throughout: cream page, Playfair headings, gold accents.

const API = "http://localhost:8000";

// Default roster (all selected on first render).
const DEFAULT_AGENTS = [
  "buffett",
  "cathie_wood",
  "peter_lynch",
  "howard_marks",
  "ray_dalio",
];

// Known agent id → display name. Unknown ids fall back to a title-cased form.
const AGENT_NAMES = {
  buffett: "Warren Buffett",
  cathie_wood: "Cathie Wood",
  peter_lynch: "Peter Lynch",
  howard_marks: "Howard Marks",
  ray_dalio: "Ray Dalio",
  munger: "Charlie Munger",
};

function displayName(ref) {
  if (!ref) return "Analyst";
  if (AGENT_NAMES[ref]) return AGENT_NAMES[ref];
  return String(ref)
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

function initials(name) {
  const parts = String(name).trim().split(/\s+/).filter(Boolean);
  const ii = (parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "");
  return ii.toUpperCase() || "?";
}

// Explicit monogram per agent id for the turn-card avatars; falls back to a
// name-derived monogram for any agent id not listed here.
const AGENT_INITIALS = {
  buffett: "WB",
  cathie_wood: "CW",
  peter_lynch: "PL",
  howard_marks: "HM",
  ray_dalio: "RD",
  synthesis: "S",
};

function agentInitials(agentId, name) {
  return AGENT_INITIALS[agentId] || initials(name);
}

// Setup-screen roster — id / display name / initials (WB, CW, PL, HM, RD).
const AVAILABLE_AGENTS = DEFAULT_AGENTS.map((id) => ({
  id,
  name: AGENT_NAMES[id],
  initials: initials(AGENT_NAMES[id]),
}));

// Tolerant SSE / NDJSON line parser: strips an optional "data:" prefix and
// parses JSON. Returns null for blank lines, comments, the "[DONE]" sentinel,
// or any non-JSON control line (e.g. SSE "event:" / ":" comment lines).
function parseEventLine(line) {
  const trimmed = line.trim();
  if (!trimmed) return null;
  const payload = trimmed.startsWith("data:") ? trimmed.slice(5).trim() : trimmed;
  if (!payload || payload === "[DONE]") return null;
  try {
    return JSON.parse(payload);
  } catch {
    return null;
  }
}

// Render a turn's response body line by line with light formatting:
//   "- " / "* " lines  → bullet with a gold dot
//   "Go verify:" line  → muted italic, and opens a muted checklist block
//   bullets after it   → muted italic checklist items (until a normal line)
// When showCursor is true (an in-flight turn), a blinking gold cursor trails the
// final line.
function renderResponse(text, showCursor) {
  const lines = String(text ?? "").split("\n");
  const lastIdx = lines.length - 1;
  let inGoVerify = false;

  // Minimal, safe inline markdown: escape HTML first, then render **bold** and
  // *italic*. Used via dangerouslySetInnerHTML for paragraph and bullet text.
  const renderInline = (raw) => {
    const safe = raw
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    return safe
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*([^*\n]+?)\*/g, "<em>$1</em>");
  };

  return lines.map((line, idx) => {
    const trimmed = line.trim();
    const isBullet = trimmed.startsWith("- ") || trimmed.startsWith("* ");
    const cursor =
      showCursor && idx === lastIdx ? (
        <span className="animate-pulse text-tikt-gold">▋</span>
      ) : null;

    // "Go verify:" header — muted italic; opens a muted checklist block.
    if (trimmed.toLowerCase().startsWith("go verify:")) {
      inGoVerify = true;
      return (
        <p
          key={idx}
          className="mt-2 text-[13px] italic leading-[1.6] text-tikt-green/50"
        >
          {trimmed}
          {cursor}
        </p>
      );
    }

    if (isBullet) {
      const content = trimmed.slice(2);
      // Go-verify bullets stay smaller and muted; regular bullets take the
      // larger body size with more generous line height.
      const cls = inGoVerify
        ? "mt-1 flex gap-2 text-[13px] leading-[1.6] italic text-tikt-green/50"
        : "mt-1 flex gap-2 text-[15px] leading-7 text-tikt-green";
      return (
        <div
          key={idx}
          className={cls}
        >
          <span className="text-tikt-gold">•</span>
          <span>
            <span dangerouslySetInnerHTML={{ __html: renderInline(content) }} />
            {cursor}
          </span>
        </div>
      );
    }

    if (trimmed === "") {
      return (
        <div key={idx} className="h-2">
          {cursor}
        </div>
      );
    }

    // A whole-line bold "**heading**" (and nothing else) → larger, spaced bold
    // paragraph. The inner-** guard avoids matching lines with two bold spans.
    const headingMatch = trimmed.match(/^\*\*(.+)\*\*$/);
    if (headingMatch && !headingMatch[1].includes("**")) {
      inGoVerify = false;
      return (
        <p
          key={idx}
          className="mt-4 text-[16px] font-bold leading-7 text-tikt-green"
        >
          <span
            dangerouslySetInnerHTML={{ __html: renderInline(headingMatch[1]) }}
          />
          {cursor}
        </p>
      );
    }

    // A normal paragraph line ends any open Go-verify block.
    inGoVerify = false;
    return (
      <p key={idx} className="mt-2 text-[15px] leading-7 text-tikt-green">
        <span dangerouslySetInnerHTML={{ __html: renderInline(line) }} />
        {cursor}
      </p>
    );
  });
}

// Collapsible "SOURCES" disclosure shown under a completed agent turn. Default
// collapsed; expands to a list of source names (max 8, "+ N more" beyond that).
// Company sources read in dark green; philosophy sources in muted italic.
const SOURCES_MAX = 8;

function SourcesSection({ sources }) {
  const [expanded, setExpanded] = useState(false);
  const extra = sources.length - SOURCES_MAX;

  return (
    <div className="mt-3 border-t border-tikt-green/10 pt-2">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[1.5px] text-tikt-gold/70 hover:text-tikt-gold"
      >
        <span
          className={`inline-block transition-transform ${
            expanded ? "rotate-90" : ""
          }`}
        >
          ›
        </span>
        Sources
      </button>
      {expanded && (
        <div className="mt-2 flex flex-col gap-1">
          {sources.slice(0, SOURCES_MAX).map((s, idx) => (
            <div
              key={idx}
              className={
                s.collection === "company"
                  ? "text-[11px] text-tikt-green"
                  : "text-[11px] italic text-tikt-green/50"
              }
            >
              {s.source}
            </div>
          ))}
          {extra > 0 && (
            <div className="text-[11px] text-tikt-green/40">+ {extra} more</div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Debate() {
  const { ticker } = useParams();
  const { state } = useLocation();
  const navigate = useNavigate();
  const symbol = (ticker || "").toUpperCase();
  const company = state?.company || symbol;

  // ── resume mode ─────────────────────────────────────────────────────────
  // Opened from History.jsx with { resume: true, session_id }. We fetch the
  // saved session and rebuild the transcript instead of showing the setup screen.
  const resumeSessionId = state?.session_id || null;
  const isResume = !!state?.resume;

  // ── setup-screen config ────────────────────────────────────────────────
  const [selectedAgents, setSelectedAgents] = useState(() => [...DEFAULT_AGENTS]);
  const [topic, setTopic] = useState(
    state?.topic || `Is ${company} a good investment?`
  );
  const [turnsPerAgent, setTurnsPerAgent] = useState(1);
  const [ingestSteps, setIngestSteps] = useState([]);

  // ── debate stream state ────────────────────────────────────────────────
  const [turns, setTurns] = useState([]);
  const [status, setStatus] = useState("idle"); // idle | ingesting | running | complete | error
  const [sessionId, setSessionId] = useState(null);
  const [error, setError] = useState(null);
  const [isLoading, setIsLoading] = useState(!!isResume); // resume fetch in flight

  // ── follow-up / multi-round state ──────────────────────────────────────
  const [followUpTopic, setFollowUpTopic] = useState("");
  const [roundNum, setRoundNum] = useState(1);
  const [allHistory, setAllHistory] = useState([]);

  // ── research-document upload state ─────────────────────────────────────
  // uploadedDocs: { filename, status: "queued" | "uploading" | "done" | "error",
  //                chunks, file }  — `file` (the actual File) is kept so a doc
  //                queued on the setup screen can be uploaded later, once the
  //                company is confirmed ingested (see flushQueuedUploads).
  const [uploadedDocs, setUploadedDocs] = useState([]);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef(null);

  const endRef = useRef(null);
  const controllerRef = useRef(null);

  const toggleAgent = (id) =>
    setSelectedAgents((prev) =>
      prev.includes(id)
        ? prev.filter((a) => a !== id)
        : DEFAULT_AGENTS.filter((x) => x === id || prev.includes(x))
    );

  // Shared streaming routine: POST a debate config and read the SSE response
  // line by line, dispatching each parsed event. `continuation` keeps the
  // existing transcript (a follow-up round) instead of starting fresh.
  const runStream = async (payload, { continuation }) => {
    if (controllerRef.current) return; // a stream is already in flight

    const controller = new AbortController();
    controllerRef.current = controller;

    setError(null);
    if (!continuation) {
      // Fresh transcript for a brand-new debate.
      setTurns([]);
      setIngestSteps([]);
      setSessionId(null);
    }
    // Follow-up rounds keep prior turns; the render logic draws the round
    // divider automatically once the new round's first turn streams in.
    setStatus("running");

    // Queued setup-screen docs, captured at stream start. They are uploaded only
    // AFTER the company is confirmed ingested (ingest_complete / session_start) so
    // they land at the engine's CURRENT ingest_version, not a stale version 0.
    // Skipped for follow-up rounds (those upload immediately — already ingested).
    const queuedAtStart = continuation
      ? []
      : uploadedDocs.filter((d) => d.status === "queued" && d.file);
    let queuedFlushStarted = false;
    const flushQueuedUploads = () => {
      if (queuedFlushStarted || queuedAtStart.length === 0) return;
      queuedFlushStarted = true;
      (async () => {
        for (const d of queuedAtStart) {
          // queued → uploading → done, keyed by filename (replaces the pill).
          await uploadDocument(d.file);
        }
      })();
    };

    const handle = (evt) => {
      if (controller.signal.aborted || !evt || !evt.type) return;
      switch (evt.type) {
        case "session_start":
          setSessionId(evt.session_id ?? evt.sessionId ?? null);
          setStatus("running");
          // Company already ingested (no ingest phase) → upload queued docs now.
          flushQueuedUploads();
          break;
        case "ingest_start":
          setStatus("ingesting");
          // Seed the steps list with the opening message (if any).
          setIngestSteps((prev) => (evt.message ? [...prev, evt.message] : prev));
          break;
        case "ingest_progress":
          // Append each progress message — the card shows the full step list.
          setIngestSteps((prev) => [...prev, evt.message]);
          break;
        case "ingest_complete":
          setStatus("running"); // debate is about to start
          // Auto-ingest just finished → the company is now at its current
          // ingest_version; upload queued docs so they land at THAT version.
          flushQueuedUploads();
          break;
        case "turn_start":
          setStatus("running");
          setTurns((prev) => [
            ...prev,
            {
              agent: evt.agent,
              display_name: evt.display_name,
              turn: evt.turn,
              response: "",
              complete: false,
              sources: [],
            },
          ]);
          break;
        case "synthesis_start":
          setStatus("running");
          setTurns((prev) => [
            ...prev,
            {
              agent: "synthesis",
              display_name: "Analyst Synthesis",
              response: "",
              complete: false,
            },
          ]);
          break;
        case "token":
        case "synthesis_token":
          setTurns((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last && !last.complete) {
              updated[updated.length - 1] = {
                ...last,
                response: last.response + (evt.token ?? ""),
              };
            }
            return updated;
          });
          break;
        case "turn_end":
        case "synthesis_end":
          setTurns((prev) => {
            const updated = [...prev];
            const last = updated[updated.length - 1];
            if (last) {
              updated[updated.length - 1] = {
                ...last,
                complete: true,
                response: evt.response ?? last.response,
              };
            }
            return updated;
          });
          break;
        case "sources":
          // Attach retrieved sources to the matching agent turn (agent + turn
          // number). Search newest-first so the turn currently streaming wins.
          setTurns((prev) => {
            const updated = [...prev];
            for (let i = updated.length - 1; i >= 0; i--) {
              const t = updated[i];
              if (
                !t.isRoundHeader &&
                !t.isSystem &&
                t.agent === evt.agent &&
                t.turn === evt.turn
              ) {
                updated[i] = { ...t, sources: evt.sources ?? [] };
                break;
              }
            }
            return updated;
          });
          break;
        case "round_complete":
          // Keep the running history so a follow-up round can pass it back as
          // session_history; the backend also persists it to MongoDB.
          setAllHistory(evt.history);
          setRoundNum((prev) => prev + 1);
          break;
        case "complete":
          setStatus("complete");
          break;
        case "error":
          setStatus("error");
          setError(evt.message ?? "The debate failed.");
          break;
        default:
          break;
      }
    };

    try {
      const response = await fetch(`${API}/debate/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });

      if (!response.ok || !response.body) {
        throw new Error(`Stream request failed (${response.status})`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (
        let chunk = await reader.read();
        !chunk.done;
        chunk = await reader.read()
      ) {
        buffer += decoder.decode(chunk.value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? ""; // retain the trailing partial line
        for (const line of lines) {
          const evt = parseEventLine(line);
          if (evt) handle(evt);
        }
      }
      // Flush any trailing buffered line once the stream closes.
      const tail = parseEventLine(buffer);
      if (tail) handle(tail);

      // If the stream ended without an explicit complete/error event, settle.
      if (!controller.signal.aborted) {
        setStatus((s) =>
          s === "running" || s === "ingesting" ? "complete" : s
        );
      }
    } catch (err) {
      if (controller.signal.aborted) return;
      setStatus("error");
      setError(err?.message || "Could not reach the debate engine.");
    } finally {
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  };

  // Initial round — fresh transcript.
  const startDebate = () => {
    if (selectedAgents.length === 0 || !topic.trim()) return;
    runStream(
      {
        ticker: symbol,
        company,
        agents: selectedAgents,
        turns: turnsPerAgent,
        topic,
      },
      { continuation: false }
    );
  };

  // Follow-up round — append to the existing transcript, passing prior history
  // so the engine can continue from where the debate left off.
  const continueDebate = () => {
    if (!followUpTopic.trim()) return;
    const followUp = followUpTopic;
    setFollowUpTopic("");
    // Insert a round header into the transcript so the new round is visually
    // separated and labelled with its follow-up topic before its turns stream in.
    setTurns((prev) => [
      ...prev,
      { isRoundHeader: true, roundNum: roundNum, topic: followUp },
    ]);
    runStream(
      {
        ticker: symbol,
        company,
        agents: selectedAgents,
        turns: turnsPerAgent,
        topic: followUp,
        session_id: sessionId,
        session_history: allHistory,
        round_num: roundNum,
      },
      { continuation: true }
    );
  };

  // ── research-document upload ───────────────────────────────────────────
  // Upload a single file immediately: POST multipart to the backend, which
  // extracts → chunks → embeds → stores it into the company's knowledge base so
  // the next debate round can cite it. Pill status is tracked per filename.
  const uploadDocument = async (fileObj) => {
    const filename = fileObj.name;
    setUploadedDocs((prev) => [
      ...prev.filter((d) => d.filename !== filename),
      { filename, status: "uploading", chunks: 0, file: fileObj },
    ]);
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", fileObj);
      form.append("ticker", symbol);
      form.append("company", company);
      const res = await fetch(`${API}/company/${symbol}/upload-document`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        let msg = `Upload failed (${res.status})`;
        try {
          const j = await res.json();
          if (j?.detail) msg = j.detail;
        } catch {
          /* non-JSON error body — keep the status-code message */
        }
        throw new Error(msg);
      }
      const data = await res.json();
      setUploadedDocs((prev) =>
        prev.map((d) =>
          d.filename === filename
            ? { ...d, status: "done", chunks: data.chunks_added ?? 0 }
            : d
        )
      );
    } catch (err) {
      setUploadedDocs((prev) =>
        prev.map((d) =>
          d.filename === filename
            ? { ...d, status: "error", error: err?.message || "Upload failed" }
            : d
        )
      );
    } finally {
      setUploading(false);
    }
  };

  // Handle chosen/dropped files. On the setup screen we QUEUE them (defer the
  // upload until the company is confirmed ingested, so docs land at the engine's
  // current ingest_version — not a stale version 0). Everywhere else (the
  // follow-up card) the company is already ingested, so upload immediately. Each
  // upload runs independently so one failure doesn't block the rest.
  const handleFiles = (fileList) => {
    const files = Array.from(fileList || []);
    if (status === "idle") {
      setUploadedDocs((prev) => {
        const next = [...prev];
        for (const f of files) {
          const entry = { filename: f.name, status: "queued", chunks: 0, file: f };
          const i = next.findIndex((d) => d.filename === f.name);
          if (i >= 0) next[i] = entry;
          else next.push(entry);
        }
        return next;
      });
    } else {
      files.forEach((f) => uploadDocument(f));
    }
  };

  // Remove a pill from the UI list (does not delete the stored chunks).
  const removeDoc = (filename) =>
    setUploadedDocs((prev) => prev.filter((d) => d.filename !== filename));

  // Resume mode — fetch the saved session and rebuild the transcript, then drop
  // straight into the "complete" state (with the follow-up box) so the user can
  // continue where the debate left off.
  useEffect(() => {
    if (!isResume || !resumeSessionId) return;
    let cancelled = false;
    fetch(`${API}/debate/${resumeSessionId}`)
      .then((r) => {
        if (!r.ok) throw new Error("not ok");
        return r.json();
      })
      .then((doc) => {
        if (cancelled) return;
        const hist = Array.isArray(doc.history) ? doc.history : [];
        const speakers = hist
          .filter((h) => h.agent !== "synthesis")
          .map((h) => ({
            agent: h.agent,
            display_name: displayName(h.agent),
            turn: h.turn,
            response: h.response,
            complete: true,
            round: h.round,
          }));
        const synth = hist
          .filter((h) => h.agent === "synthesis")
          .map((h) => ({
            agent: "synthesis",
            display_name: "Analyst Synthesis",
            turn: 0,
            response: h.response,
            complete: true,
            round: h.round,
          }));
        // Sort by round, then synthesis LAST within a round, then turn. Synthesis
        // is stored with turn:0, so a plain "round then turn" sort would render it
        // first and break the round-divider logic in PHASE 2.
        const reconstructed = [...speakers, ...synth].sort(
          (a, b) =>
            (a.round ?? 1) - (b.round ?? 1) ||
            ((a.agent === "synthesis") - (b.agent === "synthesis")) ||
            (a.turn ?? 0) - (b.turn ?? 0)
        );
        setTurns(reconstructed);
        setSessionId(doc.session_id || resumeSessionId);
        setAllHistory(hist);
        setRoundNum((doc.rounds || 1) + 1);
        if (Array.isArray(doc.agents) && doc.agents.length) {
          setSelectedAgents(doc.agents);
        }
        if (doc.topic) setTopic(doc.topic);
        setStatus("complete");
        setIsLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        setStatus("error");
        setError("Could not load this debate session.");
        setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isResume, resumeSessionId]);

  // Abort any in-flight stream on unmount. (No auto-start — the debate begins
  // only when the user clicks "Start Debate".)
  useEffect(() => () => controllerRef.current?.abort(), []);

  // Smooth-scroll the transcript to the bottom sentinel whenever a new turn is
  // added (watching turns length, not every streamed token).
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns.length, status]);

  // ───────────────────────────────────────────────────────────────────────
  // RESUME — loading the saved session (shown instead of the setup screen)
  // ───────────────────────────────────────────────────────────────────────
  if (isLoading) {
    return (
      <div className="flex min-h-0 w-full flex-1 items-center justify-center bg-tikt-cream font-inter">
        <div className="text-[14px] text-tikt-green/50">Loading debate…</div>
      </div>
    );
  }

  // ───────────────────────────────────────────────────────────────────────
  // PHASE 1 — SETUP SCREEN (full width, no sidebar)
  // ───────────────────────────────────────────────────────────────────────
  if (status === "idle" || status === "ingesting") {
    const canStart = selectedAgents.length > 0 && topic.trim().length > 0;
    return (
      <div className="min-h-0 w-full flex-1 overflow-y-auto bg-tikt-cream font-inter text-tikt-green">
        <div className="mx-auto w-full max-w-[720px] px-8 pb-24 pt-8">
          {/* back */}
          <button
            type="button"
            onClick={() => navigate(`/company/${symbol}`)}
            className="mb-7 inline-flex items-center gap-1.5 text-[13px] font-medium text-tikt-green/50 hover:text-tikt-green"
          >
            ← Back
          </button>

          {/* header */}
          <div className="text-[13px] font-semibold uppercase tracking-[2px] text-tikt-gold">
            {symbol}
          </div>
          <h1 className="mt-1.5 font-display text-[32px] font-bold leading-tight text-tikt-green">
            {company}
          </h1>
          <p className="mt-1 text-[14px] text-tikt-green/50">
            Configure your debate
          </p>

          {/* SELECT INVESTORS */}
          <div className="mt-8">
            <div className="text-[11px] font-semibold uppercase tracking-[1.5px] text-tikt-gold">
              Select Investors
            </div>
            <div className="mt-3 grid grid-cols-5 gap-3">
              {AVAILABLE_AGENTS.map((a) => {
                const sel = selectedAgents.includes(a.id);
                return (
                  <button
                    type="button"
                    key={a.id}
                    onClick={() => toggleAgent(a.id)}
                    aria-pressed={sel}
                    className={`flex flex-col items-center gap-2 rounded-lg p-4 transition ${
                      sel
                        ? "border-[1.5px] border-tikt-gold bg-tikt-gold/10"
                        : "border-[0.5px] border-tikt-green/15 bg-white hover:border-tikt-green/30"
                    }`}
                  >
                    <span className="flex h-12 w-12 items-center justify-center rounded-full bg-tikt-gold text-[15px] font-bold text-tikt-green">
                      {a.initials}
                    </span>
                    <span className="text-center text-[12px] font-medium leading-tight text-tikt-green">
                      {a.name}
                    </span>
                    <span
                      className={`flex h-4 w-4 items-center justify-center rounded-[3px] text-[10px] leading-none ${
                        sel
                          ? "bg-tikt-gold text-tikt-green"
                          : "border-[0.5px] border-tikt-green/30 text-transparent"
                      }`}
                    >
                      ✓
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* DEBATE TOPIC */}
          <div className="mt-7">
            <label
              htmlFor="debate-topic"
              className="text-[11px] font-semibold uppercase tracking-[1.5px] text-tikt-gold"
            >
              Debate Topic
            </label>
            <textarea
              id="debate-topic"
              rows={3}
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="What should the investors debate?"
              className="mt-2 w-full resize-none rounded-lg border-[0.5px] border-tikt-green/15 bg-white px-4 py-3 text-[13px] leading-[1.6] text-tikt-green outline-none placeholder:text-tikt-green/40 focus:border-tikt-green"
            />
          </div>

          {/* TURNS PER AGENT */}
          <div className="mt-7">
            <div className="text-[11px] font-semibold uppercase tracking-[1.5px] text-tikt-gold">
              Turns Per Agent
            </div>
            <div className="mt-2 flex items-center gap-3">
              <button
                type="button"
                onClick={() => setTurnsPerAgent((t) => Math.max(1, t - 1))}
                disabled={turnsPerAgent <= 1}
                className="flex h-9 w-9 items-center justify-center rounded-lg border-[0.5px] border-tikt-green/15 bg-white text-[18px] leading-none text-tikt-green hover:border-tikt-green disabled:cursor-not-allowed disabled:opacity-40"
              >
                −
              </button>
              <span className="w-8 text-center text-[16px] font-semibold tabular-nums text-tikt-green">
                {turnsPerAgent}
              </span>
              <button
                type="button"
                onClick={() => setTurnsPerAgent((t) => Math.min(5, t + 1))}
                disabled={turnsPerAgent >= 5}
                className="flex h-9 w-9 items-center justify-center rounded-lg border-[0.5px] border-tikt-green/15 bg-white text-[18px] leading-none text-tikt-green hover:border-tikt-green disabled:cursor-not-allowed disabled:opacity-40"
              >
                +
              </button>
            </div>
          </div>

          {/* START */}
          <button
            type="button"
            onClick={startDebate}
            disabled={!canStart || status === "ingesting"}
            className="mt-8 w-full rounded-none bg-tikt-green px-5 py-3.5 text-[14px] font-semibold tracking-[0.3px] text-tikt-cream hover:bg-tikt-greenDark disabled:cursor-not-allowed disabled:opacity-40"
          >
            Start Debate →
          </button>

          {/* RESEARCH DOCUMENTS — optional user uploads fed into the debate */}
          {status === "idle" && (
            <div className="mt-7">
              <div className="text-[11px] font-semibold uppercase tracking-[1.5px] text-tikt-gold">
                Research Documents
              </div>
              <div
                role="button"
                tabIndex={0}
                onClick={() => fileInputRef.current?.click()}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ")
                    fileInputRef.current?.click();
                }}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  handleFiles(e.dataTransfer.files);
                }}
                className="mt-2 cursor-pointer rounded-lg border-[1.5px] border-dashed border-tikt-gold/50 bg-tikt-cream px-5 py-6 text-center transition hover:border-tikt-gold"
              >
                <div className="text-[13px] text-tikt-green/50">
                  Drop files or click to upload
                </div>
                <div className="mt-1 text-[11px] text-tikt-green/40">
                  PDF, TXT, MD, or DOCX
                </div>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.txt,.md,.docx"
                multiple
                className="hidden"
                onChange={(e) => {
                  handleFiles(e.target.files);
                  e.target.value = "";
                }}
              />
              {uploadedDocs.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {uploadedDocs.map((d) => (
                    <span
                      key={d.filename}
                      className="inline-flex items-center gap-2 rounded-full border-[0.5px] border-tikt-green/15 bg-white px-3 py-1.5 text-[12px] text-tikt-green"
                    >
                      {d.status === "queued" && (
                        <span className="text-tikt-gold">⏳</span>
                      )}
                      {d.status === "uploading" && (
                        <span className="animate-pulse text-tikt-gold">⟳</span>
                      )}
                      {d.status === "done" && (
                        <span className="text-tikt-pos">✓</span>
                      )}
                      {d.status === "error" && (
                        <span className="text-tikt-neg">!</span>
                      )}
                      <span className={d.status === "uploading" ? "animate-pulse" : ""}>
                        {d.status === "uploading"
                          ? `Uploading ${d.filename}…`
                          : d.filename}
                      </span>
                      <button
                        type="button"
                        onClick={() => removeDoc(d.filename)}
                        className="text-tikt-green/40 hover:text-tikt-neg"
                        aria-label={`Remove ${d.filename}`}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              )}
              {uploadedDocs.some((d) => d.status === "queued") && (
                <div className="mt-2 text-[11px] italic text-tikt-green/40">
                  Will upload when debate starts
                </div>
              )}
            </div>
          )}

          {/* INGEST PROGRESS */}
          {status === "ingesting" && (
            <div className="mt-8 rounded-lg border-[1.5px] border-tikt-gold bg-white p-6">
              <h2 className="font-display text-[18px] font-bold text-tikt-green">
                Preparing Research Data
              </h2>
              <div className="mt-4 flex flex-col gap-2.5">
                {ingestSteps.length === 0 ? (
                  <div className="flex items-center gap-2.5 text-[13px] text-tikt-green">
                    <span className="animate-pulse text-tikt-gold">⟳</span>
                    <span className="animate-pulse">Starting…</span>
                  </div>
                ) : (
                  ingestSteps.map((msg, i) => {
                    const isCurrent = i === ingestSteps.length - 1;
                    return (
                      <div
                        key={i}
                        className={`flex items-start gap-2.5 text-[13px] leading-[1.5] ${
                          isCurrent ? "text-tikt-green" : "text-tikt-green/60"
                        }`}
                      >
                        <span
                          className={
                            isCurrent
                              ? "animate-pulse text-tikt-gold"
                              : "text-tikt-pos"
                          }
                        >
                          {isCurrent ? "⟳" : "✓"}
                        </span>
                        <span className={isCurrent ? "animate-pulse" : ""}>
                          {msg}
                        </span>
                      </div>
                    );
                  })
                )}
              </div>
              <p className="mt-4 text-[12px] text-tikt-green/50">
                This takes about 90 seconds on first run.
              </p>
            </div>
          )}
        </div>
      </div>
    );
  }

  // ───────────────────────────────────────────────────────────────────────
  // PHASE 2 — DEBATE VIEW (70% transcript + 30% sidebar)
  // ───────────────────────────────────────────────────────────────────────
  const loadingLabel = status === "running" ? "Debate in progress…" : null;

  return (
    <div className="flex min-h-0 w-full flex-1 overflow-hidden bg-tikt-cream font-inter text-tikt-green">
      {/* ───────────── LEFT: TRANSCRIPT (70%) ───────────── */}
      <main className="flex-1 overflow-y-auto">
        <div className="w-full max-w-none px-12 pb-24 pt-8">
          {/* back */}
          <button
            type="button"
            onClick={() => navigate(`/company/${symbol}`)}
            className="mb-7 inline-flex items-center gap-1.5 text-[13px] font-medium text-tikt-green/50 hover:text-tikt-green"
          >
            ← Back
          </button>

          {/* header */}
          <div className="text-[14px] font-semibold uppercase tracking-[2px] text-tikt-gold">
            {symbol} · Debate
          </div>
          <h1 className="mt-1.5 line-clamp-2 font-display text-[22px] font-bold leading-tight text-tikt-green">
            {topic}
          </h1>

          {/* in-progress indicator */}
          {loadingLabel && (
            <div className="mt-6 flex items-center gap-2.5 text-[13px] font-medium text-tikt-gold">
              <span className="h-2 w-2 animate-pulse rounded-full bg-tikt-gold" />
              <span className="animate-pulse">{loadingLabel}</span>
            </div>
          )}

          {/* error */}
          {status === "error" && (
            <div className="mt-6 rounded-lg border-[0.5px] border-tikt-neg/40 bg-white px-5 py-4 text-[13px] leading-[1.6] text-tikt-neg">
              {error || "Something went wrong running the debate."}
            </div>
          )}

          {/* turns — render one by one as they stream in, token by token */}
          <div className="mt-8 flex flex-col">
            {(() => {
              // Round tracking: the first non-synthesis turn that follows a
              // synthesis opens a new round, so we render a "Round n" divider
              // before it. Recomputed from scratch on every render (deterministic).
              let roundNo = 1;
              let sawSynthesis = false;

              return turns.map((t, i) => {
                // Follow-up round header — a full-width divider labelled with the
                // round number and the follow-up topic. Resetting sawSynthesis here
                // suppresses the automatic round divider for the turn that follows.
                if (t.isRoundHeader) {
                  sawSynthesis = false;
                  return (
                    <div key={i} className="my-8 flex flex-col items-center gap-3">
                      <div className="flex w-full items-center gap-4">
                        <div className="h-px flex-1 bg-tikt-gold/40" />
                        <span className="text-[11px] font-semibold uppercase tracking-[2px] text-tikt-gold">
                          Round {t.roundNum}
                        </span>
                        <div className="h-px flex-1 bg-tikt-gold/40" />
                      </div>
                      <p className="max-w-[680px] text-center font-display text-[16px] italic text-tikt-green">
                        {t.topic}
                      </p>
                    </div>
                  );
                }

                // System / ingest-progress turns render as a centered status card.
                if (t.isSystem) {
                  return (
                    <div key={i} className="my-4 flex justify-center">
                      <div className="flex items-center gap-2.5 rounded-lg border-[0.5px] border-tikt-gold bg-tikt-green/[0.03] px-5 py-3">
                        {t.complete ? (
                          <span className="text-[13px] font-semibold text-tikt-pos">
                            ✓
                          </span>
                        ) : (
                          <span className="h-2 w-2 animate-pulse rounded-full bg-tikt-gold" />
                        )}
                        <span className="text-[13px] italic text-tikt-green">
                          {t.response}
                        </span>
                      </div>
                    </div>
                  );
                }

                const isSynthesis = t.agent === "synthesis";
                const name = t.display_name || displayName(t.agent);
                const mono = agentInitials(t.agent, name);

                // A non-synthesis turn after a synthesis begins a new round.
                let divider = null;
                if (!isSynthesis && sawSynthesis) {
                  roundNo += 1;
                  sawSynthesis = false;
                  divider = (
                    <div className="my-4 flex items-center gap-4">
                      <div className="h-px flex-1 bg-tikt-green/15" />
                      <span className="text-[11px] font-semibold uppercase tracking-[2px] text-tikt-gold">
                        Round {roundNo}
                      </span>
                      <div className="h-px flex-1 bg-tikt-green/15" />
                    </div>
                  );
                }
                if (isSynthesis) sawSynthesis = true;

                return (
                  <Fragment key={i}>
                    {divider}
                    <div
                      className={`mb-6 flex gap-5 rounded-xl border border-tikt-green/15 p-6 ${
                        t.complete ? "bg-white" : "bg-tikt-gold/[0.03]"
                      } ${
                        isSynthesis ? "border-l-[3px] border-l-tikt-gold" : ""
                      }`}
                    >
                      {/* left column — avatar (80px fixed) */}
                      <div className="flex w-20 flex-shrink-0 flex-col items-center">
                        <span
                          className={`flex h-16 w-16 items-center justify-center rounded-full text-[18px] font-bold ${
                            isSynthesis
                              ? "bg-tikt-green text-tikt-cream"
                              : "bg-tikt-gold text-tikt-green"
                          }`}
                        >
                          {mono}
                        </span>
                        <span className="mt-2 text-center text-[11px] leading-tight tracking-[1px] text-tikt-green/50">
                          {isSynthesis ? "SYNTHESIS" : name}
                        </span>
                      </div>

                      {/* right column — name + response */}
                      <div className="flex-1">
                        <div
                          className={`text-[14px] font-bold ${
                            isSynthesis
                              ? "italic text-tikt-green"
                              : "text-tikt-gold"
                          }`}
                        >
                          {isSynthesis ? "ANALYST SYNTHESIS" : name}
                        </div>
                        <div className="mt-1">
                          {renderResponse(t.response, !t.complete)}
                        </div>
                        {/* retrieved sources — completed agent turns only */}
                        {!isSynthesis &&
                          t.complete &&
                          (t.sources?.length ?? 0) > 0 && (
                            <SourcesSection sources={t.sources} />
                          )}
                      </div>
                    </div>
                  </Fragment>
                );
              });
            })()}
          </div>

          {/* complete banner */}
          {status === "complete" && (
            <div className="mt-6 rounded-lg border-[0.5px] border-tikt-gold bg-tikt-gold/10 px-5 py-4 text-center text-[12px] font-semibold uppercase tracking-[2px] text-tikt-gold">
              Debate complete
            </div>
          )}

          {/* follow-up — ask another question to continue the debate */}
          {status === "complete" && (
            <div className="mt-6 rounded-lg border-[1.5px] border-tikt-gold bg-white p-5">
              <label
                htmlFor="follow-up"
                className="text-[11px] font-semibold uppercase tracking-[1.5px] text-tikt-gold"
              >
                Ask a Follow-up
              </label>
              <textarea
                id="follow-up"
                rows={2}
                value={followUpTopic}
                onChange={(e) => setFollowUpTopic(e.target.value)}
                placeholder="What else should they debate?"
                className="mt-2 w-full resize-none rounded-lg border-[0.5px] border-tikt-green/15 bg-white px-4 py-3 text-[13px] leading-[1.6] text-tikt-green outline-none placeholder:text-tikt-green/40 focus:border-tikt-green"
              />
              <button
                type="button"
                onClick={continueDebate}
                disabled={!followUpTopic.trim()}
                className="mt-3 w-full rounded-none bg-tikt-green px-5 py-3 text-[14px] font-semibold tracking-[0.3px] text-tikt-cream hover:bg-tikt-greenDark disabled:cursor-not-allowed disabled:opacity-40"
              >
                Continue Debate →
              </button>

              {/* secondary: add a research document to the next round */}
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="mt-3 text-[12px] font-medium text-tikt-gold hover:text-tikt-goldHover"
              >
                + Add research document
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.txt,.md,.docx"
                multiple
                className="hidden"
                onChange={(e) => {
                  handleFiles(e.target.files);
                  e.target.value = "";
                }}
              />
              {uploadedDocs.length > 0 && (
                <div className="mt-2 flex flex-col gap-1">
                  {uploadedDocs.map((d) => (
                    <div key={d.filename} className="text-[12px]">
                      {d.status === "uploading" && (
                        <span className="animate-pulse text-tikt-gold/70">
                          Uploading {d.filename}…
                        </span>
                      )}
                      {d.status === "done" && (
                        <span className="text-tikt-gold/70">
                          {d.filename} added to research context
                        </span>
                      )}
                      {d.status === "error" && (
                        <span className="text-tikt-neg">
                          {d.filename} failed to upload
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* scroll sentinel */}
          <div ref={endRef} />
        </div>
      </main>

      {/* ───────────── RIGHT: SESSION SIDEBAR (30%, sticky) ───────────── */}
      <aside className="w-[280px] flex-shrink-0 overflow-y-auto border-l-[0.5px] border-tikt-green/15 bg-white/40">
        <div className="sticky top-0 p-6">
          {/* status indicator */}
          <div className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[1.5px]">
            {status === "complete" ? (
              <>
                <span className="h-2 w-2 rounded-full bg-tikt-pos" />
                <span className="text-tikt-pos">Complete</span>
              </>
            ) : status === "error" ? (
              <>
                <span className="h-2 w-2 rounded-full bg-tikt-neg" />
                <span className="text-tikt-neg">Error</span>
              </>
            ) : (
              <>
                <span className="h-2 w-2 animate-pulse rounded-full bg-tikt-gold" />
                <span className="text-tikt-gold">Running…</span>
              </>
            )}
          </div>

          {/* session info card */}
          <div className="mt-5 rounded-lg border-[0.5px] border-tikt-green/15 bg-white p-5">
            <div className="text-[10px] font-semibold uppercase tracking-[1.5px] text-tikt-green/50">
              Session
            </div>

            <dl className="mt-3 flex flex-col gap-3">
              <div>
                <dt className="text-[10px] font-semibold uppercase tracking-[1px] text-tikt-green/40">
                  Ticker
                </dt>
                <dd className="mt-0.5 text-[13px] font-semibold text-tikt-green">
                  {symbol}
                </dd>
              </div>
              <div>
                <dt className="text-[10px] font-semibold uppercase tracking-[1px] text-tikt-green/40">
                  Company
                </dt>
                <dd className="mt-0.5 text-[13px] font-semibold text-tikt-green">
                  {company}
                </dd>
              </div>
              <div>
                <dt className="text-[10px] font-semibold uppercase tracking-[1px] text-tikt-green/40">
                  Topic
                </dt>
                <dd className="mt-0.5 text-[13px] leading-[1.5] text-tikt-green/80">
                  {topic}
                </dd>
              </div>
            </dl>

            {/* agents */}
            <div className="mt-5">
              <div className="text-[10px] font-semibold uppercase tracking-[1px] text-tikt-green/40">
                Agents
              </div>
              <div className="mt-3 flex flex-col gap-2.5">
                {selectedAgents.map((a) => {
                  const name = displayName(a);
                  return (
                    <div key={a} className="flex items-center gap-2.5">
                      <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-tikt-gold text-[11px] font-bold text-tikt-green">
                        {initials(name)}
                      </span>
                      <span className="text-[13px] font-medium text-tikt-green">
                        {name}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

          {/* sources placeholder */}
          <div className="mt-5 rounded-lg border-[0.5px] border-tikt-green/15 bg-white p-5">
            <div className="text-[10px] font-semibold uppercase tracking-[1.5px] text-tikt-green/50">
              Sources
            </div>
            <div className="mt-3 text-[13px] italic text-tikt-green/50">
              Loading sources…
            </div>
          </div>

          {sessionId && (
            <div className="mt-4 text-[10px] uppercase tracking-[1px] text-tikt-green/30">
              Session {sessionId}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
