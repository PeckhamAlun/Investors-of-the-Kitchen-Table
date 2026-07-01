import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import Sidebar from "../components/TickerBar";

// Statements.jsx — full financial statements for /company/:ticker/statements.
// Same editorial shell as Company.jsx. Three statement tabs (income / balance /
// cash flow) × a period toggle (Annual / Quarterly / TTM — TTM income & cash-flow
// only). The table is built ENTIRELY from whatever fields the company reports:
// the backend /statements endpoint returns raw FMP rows (+ a periodLabel), and we
// map field keys → rows dynamically (oldest period left, newest right).

const API = "https://investors-of-the-kitchen-table-production.up.railway.app";

const TABS = [
  { k: "income", label: "Income Statement" },
  { k: "balance", label: "Balance Sheet" },
  { k: "cashflow", label: "Cash Flow" },
];

// Metadata / non-line-item keys that arrive in the raw FMP rows but must never
// become table rows.
const SKIP_KEYS = new Set([
  "periodLabel", "date", "symbol", "reportedCurrency", "cik", "filingDate",
  "fillingDate", "acceptedDate", "calendarYear", "fiscalYear", "period",
  "link", "finalLink",
]);

// camelCase / PascalCase field key → human label, e.g. "costOfRevenue" → "Cost Of Revenue".
const toLabel = (key) =>
  key
    .replace(/([A-Z])/g, " $1")
    .replace(/^./, (s) => s.toUpperCase())
    .trim();

const isEps = (key) => /eps|pershare/i.test(key);
const isShares = (key) => /shs|shares/i.test(key);
const isRatio = (key) => /ratio/i.test(key);
// Summary / total lines render bold.
const isBold = (key) => /^(total|net|gross|operating|free)/i.test(key);

const num1 = (v) =>
  (v / 1e6).toLocaleString("en-US", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });

// Format one cell value for a given field key. Returns { text, tone }.
function fmtCell(key, v) {
  if (v == null || typeof v !== "number" || Number.isNaN(v)) {
    return { text: "—", tone: "text-tikt-green/50" };
  }
  let text;
  if (isEps(key)) text = v.toFixed(2); // per-share: raw, not in millions
  else if (isShares(key)) text = `${num1(v)}m shares`;
  else text = num1(v); // dollar amounts in millions, 1 decimal

  const tone =
    v < 0 ? "text-red-600" : v === 0 ? "text-tikt-green/50" : "text-tikt-green";
  return { text, tone };
}

// Tolerant SSE/NDJSON line parser (mirrors Debate.jsx): strips an optional
// "data:" prefix and parses JSON; returns null for blank / comment / [DONE] lines.
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

// Inline icons — kept local so Statements has no extra deps.
function DownloadIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="#C9A84C"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="flex-shrink-0"
      aria-hidden="true"
    >
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

// ResearchModal — centered overlay that builds and downloads a company research
// ZIP (SEC filings + earnings transcripts). Streams build progress from
// POST /company/:ticker/prepare-research over SSE, then triggers the download
// from GET /company/:ticker/download-research/:downloadId once the ZIP is ready.
function ResearchModal({ ticker, symbol, companyName, onClose }) {
  const [years, setYears] = useState(5);
  const [customYears, setCustomYears] = useState("");
  const [useCustom, setUseCustom] = useState(false);
  const [status, setStatus] = useState("idle"); // idle | preparing | ready | error
  const [progressSteps, setProgressSteps] = useState([]);
  const [downloadId, setDownloadId] = useState(null);
  const [errorMessage, setErrorMessage] = useState("");

  const preparing = status === "preparing";

  // Effective year count: the custom input (clamped 1–20) when "Custom" is
  // active and valid, otherwise the selected preset. 0 signals an invalid custom.
  const parsedCustom = parseInt(customYears, 10);
  const customValid =
    Number.isFinite(parsedCustom) && parsedCustom >= 1 && parsedCustom <= 20;
  const effectiveYears = useCustom ? (customValid ? parsedCustom : 0) : years;

  // Estimated document count: one 10-K per year, four 10-Qs per year (capped at
  // 20), plus 12 earnings-call transcripts.
  const estimate = effectiveYears
    ? effectiveYears + Math.min(effectiveYears * 4, 20) + 12
    : 0;

  const handleEvent = (evt) => {
    if (evt.type === "progress") {
      setProgressSteps((prev) => [...prev, evt.message]);
    } else if (evt.type === "ready") {
      setDownloadId(evt.download_id);
      setStatus("ready");
      // Trigger the browser download of the finished ZIP.
      window.location.href = `${API}/company/${ticker}/download-research/${evt.download_id}`;
    } else if (evt.type === "error") {
      setStatus("error");
      setErrorMessage(evt.message || "Failed to build package.");
    }
  };

  const handleDownload = async () => {
    if (preparing) return;
    if (!effectiveYears) {
      setStatus("error");
      setErrorMessage("Enter a valid number of years (1–20).");
      return;
    }
    setStatus("preparing");
    setProgressSteps([]);
    setErrorMessage("");
    setDownloadId(null);

    try {
      const response = await fetch(`${API}/company/${ticker}/prepare-research`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ years: effectiveYears }),
      });
      if (!response.ok || !response.body) {
        throw new Error(`Request failed (${response.status})`);
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
          if (evt) handleEvent(evt);
        }
      }
      const tail = parseEventLine(buffer);
      if (tail) handleEvent(tail);
    } catch (err) {
      setStatus("error");
      setErrorMessage(err?.message || "Could not reach the server.");
    }
  };

  const pill = (active) =>
    `rounded-full px-4 py-1.5 text-[12px] font-semibold transition ${
      active
        ? "bg-tikt-gold text-tikt-green"
        : "border-[0.5px] border-tikt-green/20 bg-tikt-cream text-tikt-green/60 hover:text-tikt-green"
    }`;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[85vh] w-full max-w-md overflow-y-auto rounded-xl bg-white p-8 shadow-[0_20px_60px_rgba(16,35,26,0.25)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* header row */}
        <div className="flex items-start justify-between gap-4">
          <h2 className="font-display text-[20px] font-bold leading-tight text-tikt-green">
            Download Research Package
          </h2>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="-mr-1 -mt-1 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full text-[18px] leading-none text-tikt-green/50 hover:bg-tikt-green/5 hover:text-tikt-green"
          >
            ×
          </button>
        </div>
        <div className="mt-1 text-[12px] font-semibold uppercase tracking-[1.5px] text-tikt-gold">
          {companyName || symbol} · {symbol}
        </div>

        <div className="mt-4 border-t-[0.5px] border-tikt-green/15" />

        {/* TIMELINE */}
        <div className="mt-5">
          <div className="text-[11px] font-semibold uppercase tracking-[1.5px] text-tikt-gold">
            Timeline
          </div>
          <div className="mt-2.5 flex flex-wrap gap-2">
            {[1, 3, 5].map((y) => (
              <button
                key={y}
                type="button"
                disabled={preparing}
                onClick={() => {
                  setUseCustom(false);
                  setYears(y);
                }}
                className={pill(!useCustom && years === y)}
              >
                {y}Y
              </button>
            ))}
            <button
              type="button"
              disabled={preparing}
              onClick={() => setUseCustom(true)}
              className={pill(useCustom)}
            >
              Custom
            </button>
          </div>
          {useCustom && (
            <input
              type="number"
              min={1}
              max={20}
              value={customYears}
              disabled={preparing}
              onChange={(e) => setCustomYears(e.target.value)}
              placeholder="Years (1–20)"
              className="mt-3 w-36 rounded-md border-[0.5px] border-tikt-green/20 bg-tikt-cream px-3 py-1.5 text-[13px] text-tikt-green outline-none focus:border-tikt-gold"
            />
          )}
        </div>

        {/* INCLUDES */}
        <div className="mt-6">
          <div className="text-[11px] font-semibold uppercase tracking-[1.5px] text-tikt-gold">
            Includes
          </div>
          <div className="mt-2.5 flex flex-col gap-2">
            {[
              "10-K Annual Reports",
              "10-Q Quarterly Reports",
              "Earnings Call Transcripts (12 quarters)",
            ].map((label) => (
              <div
                key={label}
                className="flex items-center gap-2.5 text-[13px] text-tikt-green"
              >
                <CheckIcon />
                <span>{label}</span>
              </div>
            ))}
          </div>
        </div>

        {/* ESTIMATED FILES */}
        <div className="mt-5 text-[13px] text-tikt-green/50">
          ~{estimate} documents
        </div>

        {/* DOWNLOAD BUTTON */}
        <button
          type="button"
          onClick={handleDownload}
          disabled={preparing}
          className="mt-5 flex w-full items-center justify-center gap-2 rounded-md bg-tikt-green px-5 py-3 text-[14px] font-semibold text-tikt-cream hover:bg-tikt-greenDark disabled:cursor-not-allowed disabled:opacity-70"
        >
          {status === "preparing" ? (
            <>
              <span className="h-2 w-2 animate-pulse rounded-full bg-tikt-cream" />
              Preparing package…
            </>
          ) : status === "ready" ? (
            "Package Ready — Downloading…"
          ) : (
            "Download Research Package →"
          )}
        </button>

        {/* ERROR */}
        {status === "error" && errorMessage && (
          <div className="mt-3 text-[13px] font-medium text-red-600">
            {errorMessage}
          </div>
        )}

        {/* PROGRESS DISPLAY */}
        {(status === "preparing" || status === "ready") &&
          progressSteps.length > 0 && (
            <div className="mt-4 flex flex-col gap-2">
              {progressSteps.map((step, i) => {
                const isLast = i === progressSteps.length - 1;
                return (
                  <div
                    key={i}
                    className="flex items-center gap-2.5 text-[13px] text-tikt-green/60"
                  >
                    <span
                      className={`h-1.5 w-1.5 flex-shrink-0 rounded-full bg-tikt-gold ${
                        isLast && status === "preparing" ? "animate-pulse" : ""
                      }`}
                    />
                    <span>{step}</span>
                  </div>
                );
              })}
            </div>
          )}
      </div>
    </div>
  );
}

export default function Statements() {
  const { ticker } = useParams();
  const navigate = useNavigate();
  const symbol = (ticker || "").toUpperCase();

  const [profile, setProfile] = useState(null);
  const [tab, setTab] = useState("income");
  const [period, setPeriod] = useState("annual");
  const [rows, setRows] = useState([]);
  const [status, setStatus] = useState("loading"); // loading | ok | error
  const [showModal, setShowModal] = useState(false);

  // Company name / exchange — once per ticker.
  useEffect(() => {
    fetch(`${API}/company/${ticker}/profile`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setProfile(d))
      .catch(() => setProfile(null));
  }, [ticker]);

  // Statement rows — refetch on ticker / tab / period change.
  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    fetch(`${API}/company/${ticker}/statements?type=${tab}&period=${period}`)
      .then((r) => {
        if (!r.ok) throw new Error("not ok");
        return r.json();
      })
      .then((d) => {
        if (cancelled) return;
        setRows(Array.isArray(d) ? d : []);
        setStatus("ok");
      })
      .catch(() => {
        if (cancelled) return;
        setRows([]);
        setStatus("error");
      });
    return () => {
      cancelled = true;
    };
  }, [ticker, tab, period]);

  // TTM only applies to income & cash flow. Switching to the balance sheet while
  // on TTM drops back to Annual.
  const selectTab = (k) => {
    setTab(k);
    if (k === "balance" && period === "ttm") setPeriod("annual");
  };

  const ttmAllowed = tab !== "balance";
  const periodOptions = [
    { k: "annual", label: "Annual" },
    { k: "quarterly", label: "Quarterly" },
    ...(ttmAllowed ? [{ k: "ttm", label: "TTM" }] : []),
  ];

  // Ordered list of field-key rows: first-occurrence order across all periods
  // (the first period's logical FMP order dominates), minus metadata / ratio
  // keys, keeping only fields that are numeric in at least one period.
  const fieldKeys = (() => {
    if (!rows.length) return [];
    const ordered = [];
    const seen = new Set();
    for (const row of rows) {
      for (const k of Object.keys(row)) {
        if (!seen.has(k)) {
          seen.add(k);
          ordered.push(k);
        }
      }
    }
    return ordered.filter((k) => {
      if (SKIP_KEYS.has(k) || isRatio(k)) return false;
      return rows.some(
        (row) => typeof row[k] === "number" && !Number.isNaN(row[k])
      );
    });
  })();

  return (
    <div className="flex min-h-0 w-full flex-1 overflow-hidden font-inter text-tikt-green">
      <Sidebar />

      <main className="flex-1 overflow-y-auto bg-tikt-cream">
        <div className="mx-auto w-full max-w-[1000px] px-8 pb-20 pt-8">
          {/* back */}
          <button
            type="button"
            onClick={() => navigate(`/company/${ticker}`)}
            className="mb-7 inline-flex items-center gap-1.5 text-[13px] font-medium text-tikt-green/50 hover:text-tikt-green"
          >
            ← {symbol}
          </button>

          {/* header */}
          <div className="text-[11px] font-semibold uppercase tracking-[2px] text-tikt-gold">
            {profile?.exchange ? `${profile.exchange} · ${symbol}` : symbol}
          </div>
          <div className="mt-1.5 flex items-center justify-between gap-4">
            <h1 className="font-display text-[28px] font-bold leading-tight text-tikt-green">
              {profile?.name || symbol}
            </h1>
            <button
              type="button"
              onClick={() => setShowModal(true)}
              className="inline-flex flex-shrink-0 items-center gap-1.5 rounded-md border border-tikt-gold bg-tikt-cream px-3 py-1.5 text-[12px] font-semibold text-tikt-gold transition hover:bg-tikt-gold/10"
            >
              <DownloadIcon />
              Research Package
            </button>
          </div>
          <div className="mt-1 text-[13px] text-tikt-green/50">
            Financial Statements
          </div>

          {/* statement tabs */}
          <div className="mt-7 flex gap-6 border-b border-tikt-green/15">
            {TABS.map((t) => {
              const active = tab === t.k;
              return (
                <button
                  key={t.k}
                  type="button"
                  onClick={() => selectTab(t.k)}
                  className={`-mb-px border-b-2 pb-3 text-[14px] font-semibold ${
                    active
                      ? "border-tikt-gold text-tikt-green"
                      : "border-transparent text-tikt-green/50 hover:text-tikt-green"
                  }`}
                >
                  {t.label}
                </button>
              );
            })}
          </div>

          {/* period toggle */}
          <div className="mt-5 flex w-fit rounded border-[0.5px] border-tikt-green/15 p-0.5">
            {periodOptions.map((opt) => {
              const active = period === opt.k;
              return (
                <button
                  key={opt.k}
                  type="button"
                  onClick={() => setPeriod(opt.k)}
                  className={`rounded-[3px] px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.5px] ${
                    active
                      ? "bg-tikt-gold/15 text-tikt-gold"
                      : "text-tikt-green/50 hover:text-tikt-green"
                  }`}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>

          {/* table card */}
          <div className="mt-5 overflow-hidden rounded-lg border-[0.5px] border-tikt-green/15 bg-white">
            <div className="overflow-x-auto">
              {status === "loading" ? (
                <SkeletonTable />
              ) : status === "error" ? (
                <div className="px-6 py-10 text-center text-[13px] text-tikt-green/50">
                  Could not load statements
                </div>
              ) : rows.length === 0 || fieldKeys.length === 0 ? (
                <div className="px-6 py-10 text-center text-[13px] text-tikt-green/50">
                  No data available
                </div>
              ) : (
                <table className="w-full border-collapse">
                  <thead>
                    <tr className="border-b border-tikt-gold/20">
                      <th className="w-[220px] px-6 py-2.5 text-left text-[11px] font-semibold uppercase tracking-[1px] text-tikt-green/50">
                        Metric
                      </th>
                      {rows.map((row, i) => (
                        <th
                          key={i}
                          className={`min-w-[100px] whitespace-nowrap px-4 py-2.5 text-right text-[11px] font-semibold uppercase tracking-[1px] ${
                            i === rows.length - 1
                              ? "text-tikt-gold"
                              : "text-tikt-green/50"
                          }`}
                        >
                          {row.periodLabel}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {fieldKeys.map((key, ri) => {
                      const bold = isBold(key);
                      return (
                        <tr
                          key={key}
                          className={ri % 2 === 1 ? "bg-tikt-green/[0.02]" : ""}
                        >
                          <td
                            className={`w-[220px] px-6 py-3 text-left text-[13px] text-tikt-green ${
                              bold ? "font-bold" : "font-medium"
                            }`}
                          >
                            {toLabel(key)}
                          </td>
                          {rows.map((row, ci) => {
                            const { text, tone } = fmtCell(key, row[key]);
                            return (
                              <td
                                key={ci}
                                className={`min-w-[100px] whitespace-nowrap px-4 py-3 text-right text-[13px] tabular-nums ${tone} ${
                                  bold ? "font-bold" : ""
                                }`}
                              >
                                {text}
                              </td>
                            );
                          })}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>

        {showModal && (
          <ResearchModal
            ticker={ticker}
            symbol={symbol}
            companyName={profile?.name}
            onClose={() => setShowModal(false)}
          />
        )}
      </main>
    </div>
  );
}

// Skeleton placeholder rows while the statement loads.
function SkeletonTable() {
  return (
    <div className="px-6 py-5">
      {Array.from({ length: 8 }).map((_, r) => (
        <div key={r} className="flex items-center gap-4 py-2.5">
          <div className="h-3 w-[200px] flex-shrink-0 animate-pulse rounded bg-tikt-green/10" />
          <div className="flex flex-1 justify-end gap-6">
            {Array.from({ length: 5 }).map((_, c) => (
              <div
                key={c}
                className="h-3 w-[70px] animate-pulse rounded bg-tikt-green/10"
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
