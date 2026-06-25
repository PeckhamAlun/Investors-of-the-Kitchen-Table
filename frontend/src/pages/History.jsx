import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import Sidebar from "../components/TickerBar";

// History.jsx — /history. A global list of every past debate session across all
// companies, grouped by ticker, in the TIKT editorial look (cream page, Playfair
// heading, gold accents). Clicking a card resumes that saved session: it opens
// /debate/:ticker with { resume: true, session_id } in router state, and Debate.jsx
// rebuilds the full transcript from stored history.

const API = "http://localhost:8000";

// Known agent id → display name (mirrors Debate.jsx). Unknown ids title-case.
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

// ISO-8601 (stored UTC) → "25 Jun 2026, 8:42pm".
function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const month = d.toLocaleString("en-GB", { month: "short" });
  let h = d.getHours();
  const m = String(d.getMinutes()).padStart(2, "0");
  const ampm = h >= 12 ? "pm" : "am";
  h = h % 12 || 12;
  return `${d.getDate()} ${month} ${d.getFullYear()}, ${h}:${m}${ampm}`;
}

// Group debates (already newest-first from the API) by ticker. A Map keeps
// insertion order, so groups sort by their most-recent debate and each group's
// debates stay newest-first.
function groupByTicker(debates) {
  const groups = new Map();
  for (const d of debates) {
    const key = d.ticker || d.company || "—";
    if (!groups.has(key)) {
      groups.set(key, { ticker: d.ticker || key, company: d.company || "", debates: [] });
    }
    groups.get(key).debates.push(d);
  }
  return [...groups.values()];
}

export default function History() {
  const navigate = useNavigate();
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetch(`${API}/debates`)
      .then((r) => {
        if (!r.ok) throw new Error("not ok");
        return r.json();
      })
      .then((d) => {
        setHistory(Array.isArray(d) ? d : []);
        setLoading(false);
      })
      .catch(() => {
        setError(true);
        setLoading(false);
      });
  }, []);

  const groups = groupByTicker(history);

  return (
    <div className="flex min-h-0 w-full flex-1 overflow-hidden font-sans text-tikt-ink">
      <Sidebar />
      <main className="flex-1 overflow-y-auto bg-tikt-cream">
        <div className="mx-auto w-full max-w-[920px] px-10 pb-24 pt-10">
          {/* header */}
          <h1 className="font-display text-[32px] font-bold leading-tight text-tikt-green">
            Debate History
          </h1>
          <p className="mt-1 text-[14px] text-tikt-muted">
            Your past research sessions
          </p>

          {/* states */}
          {loading && (
            <div className="mt-12 text-[13px] text-tikt-green/50">Loading…</div>
          )}

          {!loading && error && (
            <div className="mt-12 text-[14px] text-tikt-muted">
              Could not load debate history.
            </div>
          )}

          {!loading && !error && groups.length === 0 && (
            <div className="mt-12 text-[14px] text-tikt-muted">
              No debates yet. Start your first debate from any company page.
            </div>
          )}

          {/* grouped debates */}
          {!loading && !error && groups.length > 0 && (
            <div className="mt-10 flex flex-col gap-9">
              {groups.map((g) => (
                <section key={g.ticker}>
                  <div className="mb-3 text-[12px] font-bold uppercase tracking-[1.5px] text-tikt-gold">
                    {g.company ? `${g.company} · ${g.ticker}` : g.ticker}
                  </div>

                  <div className="flex flex-col gap-3">
                    {g.debates.map((d) => {
                      const rounds = d.rounds ?? 1;
                      const agents = Array.isArray(d.agents) ? d.agents : [];
                      return (
                        <div
                          key={d.session_id}
                          onClick={() =>
                            navigate(`/debate/${encodeURIComponent(d.ticker)}`, {
                              state: {
                                session_id: d.session_id,
                                resume: true,
                                company: d.company,
                                topic: d.topic,
                                agents: d.agents,
                              },
                            })
                          }
                          className="cursor-pointer rounded-lg border-[0.5px] border-tikt-green/15 bg-white p-4 transition hover:border-tikt-gold hover:shadow-[0_8px_22px_rgba(64,52,24,0.08)]"
                        >
                          <div className="flex items-start justify-between gap-4">
                            <div className="text-[15px] font-medium leading-snug text-tikt-green">
                              {d.topic ||
                                `Is ${d.ticker} a good investment?`}
                            </div>
                            {rounds > 1 && (
                              <span className="flex-shrink-0 rounded-full bg-tikt-gold/15 px-2 py-0.5 text-[11px] font-semibold text-tikt-gold">
                                {rounds} rounds
                              </span>
                            )}
                          </div>

                          <div className="mt-3 flex items-center justify-between gap-4">
                            {/* agent initials circles */}
                            <div className="flex items-center gap-1.5">
                              {agents.map((a) => (
                                <span
                                  key={a}
                                  title={displayName(a)}
                                  className="flex h-7 w-7 items-center justify-center rounded-full bg-tikt-gold text-[11px] font-bold text-tikt-green"
                                >
                                  {initials(displayName(a))}
                                </span>
                              ))}
                            </div>

                            <div className="text-[12px] text-tikt-faint">
                              {formatDate(d.created_at)}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </section>
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
