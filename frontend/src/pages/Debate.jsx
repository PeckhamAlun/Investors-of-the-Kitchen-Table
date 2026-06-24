import { useEffect, useRef, useState } from "react";
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
      const tone = inGoVerify ? "italic text-tikt-green/50" : "text-tikt-green";
      return (
        <div
          key={idx}
          className={`mt-1 flex gap-2 text-[13px] leading-[1.6] ${tone}`}
        >
          <span className="text-tikt-gold">•</span>
          <span>
            {content}
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

    // A normal paragraph line ends any open Go-verify block.
    inGoVerify = false;
    return (
      <p key={idx} className="mt-2 text-[13px] leading-[1.6] text-tikt-green">
        {line}
        {cursor}
      </p>
    );
  });
}

export default function Debate() {
  const { ticker } = useParams();
  const { state } = useLocation();
  const navigate = useNavigate();
  const symbol = (ticker || "").toUpperCase();
  const company = state?.company || symbol;

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

  const endRef = useRef(null);
  const controllerRef = useRef(null);

  const toggleAgent = (id) =>
    setSelectedAgents((prev) =>
      prev.includes(id)
        ? prev.filter((a) => a !== id)
        : DEFAULT_AGENTS.filter((x) => x === id || prev.includes(x))
    );

  // Kick off the debate when the user clicks Start: POST the chosen config and
  // read the streaming response line by line, dispatching each parsed SSE event.
  const startDebate = async () => {
    if (selectedAgents.length === 0 || !topic.trim()) return;
    if (controllerRef.current) return; // a stream is already in flight

    const controller = new AbortController();
    controllerRef.current = controller;

    // Reset transcript state and move into the debate view.
    setTurns([]);
    setIngestSteps([]);
    setSessionId(null);
    setError(null);
    setStatus("running");

    const handle = (evt) => {
      if (controller.signal.aborted || !evt || !evt.type) return;
      switch (evt.type) {
        case "session_start":
          setSessionId(evt.session_id ?? evt.sessionId ?? null);
          setStatus("running");
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
        case "round_complete":
          // No action — the backend persists the finished round to MongoDB.
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
        body: JSON.stringify({
          ticker: symbol,
          company,
          agents: selectedAgents,
          turns: turnsPerAgent,
          topic,
        }),
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

  // Abort any in-flight stream on unmount. (No auto-start — the debate begins
  // only when the user clicks "Start Debate".)
  useEffect(() => () => controllerRef.current?.abort(), []);

  // Smooth-scroll the transcript to the bottom sentinel whenever a new turn is
  // added (watching turns length, not every streamed token).
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns.length, status]);

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
      <main className="w-[70%] overflow-y-auto">
        <div className="mx-auto w-full max-w-[760px] px-10 pb-24 pt-8">
          {/* back */}
          <button
            type="button"
            onClick={() => navigate(`/company/${symbol}`)}
            className="mb-7 inline-flex items-center gap-1.5 text-[13px] font-medium text-tikt-green/50 hover:text-tikt-green"
          >
            ← Back
          </button>

          {/* header */}
          <div className="text-[11px] font-semibold uppercase tracking-[2px] text-tikt-gold">
            {symbol} · Debate
          </div>
          <h1 className="mt-1.5 font-display text-[28px] font-bold leading-tight text-tikt-green">
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
            {turns.map((t, i) => {
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
              return (
                <div
                  key={i}
                  className={`py-6 ${
                    i > 0 ? "border-t-[0.5px] border-tikt-green/15" : ""
                  }`}
                >
                  <div
                    className={`text-[14px] font-bold ${
                      isSynthesis ? "italic text-tikt-green" : "text-tikt-gold"
                    }`}
                  >
                    {isSynthesis ? "ANALYST SYNTHESIS" : name}
                  </div>
                  <div className="mt-1">
                    {renderResponse(t.response, !t.complete)}
                  </div>
                </div>
              );
            })}
          </div>

          {/* complete banner */}
          {status === "complete" && (
            <div className="mt-6 rounded-lg border-[0.5px] border-tikt-gold bg-tikt-gold/10 px-5 py-4 text-center text-[12px] font-semibold uppercase tracking-[2px] text-tikt-gold">
              Debate complete
            </div>
          )}

          {/* scroll sentinel */}
          <div ref={endRef} />
        </div>
      </main>

      {/* ───────────── RIGHT: SESSION SIDEBAR (30%, sticky) ───────────── */}
      <aside className="w-[30%] flex-shrink-0 overflow-y-auto border-l-[0.5px] border-tikt-green/15 bg-white/40">
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
