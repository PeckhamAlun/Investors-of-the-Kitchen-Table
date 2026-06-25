import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import Sidebar from "../components/TickerBar";

// Statements.jsx — full financial statements for /company/:ticker/statements.
// Same editorial shell as Company.jsx. Three statement tabs (income / balance /
// cash flow) × a period toggle (Annual / Quarterly / TTM — TTM income & cash-flow
// only). The table is built ENTIRELY from whatever fields the company reports:
// the backend /statements endpoint returns raw FMP rows (+ a periodLabel), and we
// map field keys → rows dynamically (oldest period left, newest right).

const API = "http://localhost:8000";

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

export default function Statements() {
  const { ticker } = useParams();
  const navigate = useNavigate();
  const symbol = (ticker || "").toUpperCase();

  const [profile, setProfile] = useState(null);
  const [tab, setTab] = useState("income");
  const [period, setPeriod] = useState("annual");
  const [rows, setRows] = useState([]);
  const [status, setStatus] = useState("loading"); // loading | ok | error

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
          <h1 className="mt-1.5 font-display text-[28px] font-bold leading-tight text-tikt-green">
            {profile?.name || symbol}
          </h1>
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
