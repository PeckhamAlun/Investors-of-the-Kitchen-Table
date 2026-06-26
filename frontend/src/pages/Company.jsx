import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import Sidebar from "../components/TickerBar";

// Company.jsx — equity research overview for /company/:ticker. Editorial TIKT
// look: cream page, white cards with hairline green borders, Playfair Display
// headings, gold accents. Profile, price history, key metrics and the financial
// table are all loaded live from the FastAPI backend; each section loads
// independently so the page shell renders immediately.

const API = "https://investors-of-the-kitchen-table-production.up.railway.app";

const RANGES = ["1W", "1M", "YTD", "1Y", "3Y", "5Y", "10Y", "Max"];

// Metrics grid — maps each card label to a key on the /metrics response.
const METRIC_CARDS = [
  { label: "Market Cap", key: "market_cap" },
  { label: "EV / Revenue", key: "ev_revenue" },
  { label: "P/E (Fwd)", key: "pe_forward" },
  { label: "Gross Margin", key: "gross_margin" },
  { label: "FCF Margin", key: "fcf_margin" },
  { label: "Price / Sales", key: "price_to_sales" },
  { label: "Debt / Equity", key: "debt_to_equity" },
  { label: "Current Ratio", key: "current_ratio" },
];

// Financial table — columns rendered after the leading Metric column. Keys match
// the /financials row shape from the backend.
const FIN_COLUMNS = [
  { key: "revenue", label: "Revenue" },
  { key: "gross_profit", label: "Gross Profit" },
  { key: "gross_margin", label: "Gross Margin %" },
  { key: "operating_income", label: "Op. Income" },
  { key: "net_income", label: "Net Income" },
  { key: "fcf", label: "Free Cash Flow" },
];

const AGENTS = [
  "Warren Buffett",
  "Charlie Munger",
  "Peter Lynch",
  "Michael Burry",
  "Cathie Wood",
];

const EXCERPTS = [
  {
    agent: "Peter Lynch",
    quote: "The growth is real, but I'd want to see the operating margin inflect before paying this multiple.",
  },
  {
    agent: "Cathie Wood",
    quote: "This is exactly the kind of platform shift the market is underpricing on a five-year horizon.",
  },
];

// A formatted money string is negative when wrapped in parens or carrying a
// minus sign (the backend renders negatives as e.g. "$-84.3M"). "N/A" is muted.
function moneyTone(value) {
  const v = String(value ?? "").trim();
  if (v === "" || v === "N/A") return "text-tikt-green/50";
  return v.startsWith("(") || v.includes("-") || v.includes("−")
    ? "text-red-600"
    : "text-tikt-green";
}

// Company logo — circular FMP logo on white; falls back to the ticker's first
// two characters (gold on dark green) when there is no logo URL or the image
// fails to load.
function CompanyLogo({ logo, name, ticker }) {
  const [errored, setErrored] = useState(false);
  const initials = (ticker || "").slice(0, 2).toUpperCase();

  if (!logo || errored) {
    return (
      <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full border border-tikt-green/15 bg-tikt-green text-[15px] font-bold tracking-[0.5px] text-tikt-gold">
        {initials}
      </div>
    );
  }

  return (
    <img
      src={logo}
      alt={name || ticker}
      onError={() => setErrored(true)}
      className="h-12 w-12 shrink-0 rounded-full border border-tikt-green/15 bg-white object-contain p-1"
    />
  );
}

export default function Company() {
  const { ticker } = useParams();
  const navigate = useNavigate();
  const symbol = (ticker || "").toUpperCase();

  const [profile, setProfile] = useState(null);
  const [priceHistory, setPriceHistory] = useState([]);
  const [metrics, setMetrics] = useState(null);
  const [financials, setFinancials] = useState({
    quarterly: [],
    annual: [],
    ttm: [],
  });
  const [timeRange, setTimeRange] = useState("YTD");
  const [financialView, setFinancialView] = useState("quarterly");
  const [loading, setLoading] = useState({
    profile: true,
    chart: true,
    metrics: true,
    financials: true,
  });
  const [error, setError] = useState(null);

  // Profile — on mount / ticker change. A failed load (network error or a
  // non-OK status such as a 404 for an unknown ticker) flips the error state.
  useEffect(() => {
    setError(null);
    fetch(`${API}/company/${ticker}/profile`)
      .then((r) => {
        if (!r.ok) throw new Error("not ok");
        return r.json();
      })
      .then((d) => {
        setProfile(d);
        setLoading((l) => ({ ...l, profile: false }));
      })
      .catch(() => setError("Failed to load company data"));
  }, [ticker]);

  // Price history — refetches whenever the ticker or selected range changes.
  useEffect(() => {
    setLoading((l) => ({ ...l, chart: true }));
    fetch(`${API}/company/${ticker}/price-history?range=${timeRange}`)
      .then((r) => r.json())
      .then((d) => {
        setPriceHistory(d);
        setLoading((l) => ({ ...l, chart: false }));
      })
      .catch(() => setLoading((l) => ({ ...l, chart: false })));
  }, [ticker, timeRange]);

  // Key metrics — on mount / ticker change.
  useEffect(() => {
    fetch(`${API}/company/${ticker}/metrics`)
      .then((r) => r.json())
      .then((d) => {
        setMetrics(d);
        setLoading((l) => ({ ...l, metrics: false }));
      })
      .catch(() => setLoading((l) => ({ ...l, metrics: false })));
  }, [ticker]);

  // Financial statements — on mount / ticker change.
  useEffect(() => {
    fetch(`${API}/company/${ticker}/financials`)
      .then((r) => r.json())
      .then((d) => {
        setFinancials(d);
        setLoading((l) => ({ ...l, financials: false }));
      })
      .catch(() => setLoading((l) => ({ ...l, financials: false })));
  }, [ticker]);

  const chartData = Array.isArray(priceHistory) ? priceHistory : [];
  const finSource =
    financialView === "quarterly"
      ? financials.quarterly
      : financialView === "annual"
      ? financials.annual
      : financials.ttm;
  // Quarterly/annual arrive newest-first → reverse to display oldest → newest
  // (ascending L→R). TTM already arrives oldest-first from the backend, so render
  // it as-is — reversing it again would flip it back to newest-first.
  const finArr = Array.isArray(finSource) ? finSource : [];
  const finRows =
    financialView === "ttm" ? finArr.slice() : finArr.slice().reverse();

  // ── Margins trend table — 4 margin rows across the selected view's periods ──
  // Parse a backend money string ("$1.2B", "$-84.3M", "N/A") to a Number,
  // honouring parenthesised / leading-minus negatives.
  const parseM = (s) => {
    if (!s || s === "N/A") return null;
    const neg = s.includes("(") || s.startsWith("-");
    const n = parseFloat(s.replace(/[$(),BMK]/g, ""));
    if (isNaN(n)) return null;
    const abs = s.includes("B")
      ? n * 1e9
      : s.includes("M")
      ? n * 1e6
      : s.includes("K")
      ? n * 1e3
      : n;
    return neg ? -abs : abs;
  };

  // The margins table shares the financial table's view-aware rows (finRows), so
  // the single Quarterly/Annual/TTM toggle drives both tables at once.
  const ratio = (numStr, revStr) => {
    const num = parseM(numStr);
    const rev = parseM(revStr);
    if (num == null || rev == null || rev === 0) return null;
    return num / rev;
  };
  const fmtPct = (v) => (v == null ? "N/A" : `${(v * 100).toFixed(1)}%`);
  const numTone = (v) =>
    v == null ? "text-tikt-green/50" : v < 0 ? "text-red-600" : "text-tikt-green";
  const strTone = (s) =>
    !s || s === "N/A"
      ? "text-tikt-green/50"
      : s.includes("-") || s.includes("−") || s.startsWith("(")
      ? "text-red-600"
      : "text-tikt-green";

  // Each margin row: a label + one {value, tone} cell per period (oldest→newest).
  // Gross margin is already formatted by the backend; the rest are computed here.
  const marginRows = [
    {
      label: "Gross Margin",
      cells: finRows.map((row) => ({
        value:
          row.gross_margin && row.gross_margin !== "N/A"
            ? row.gross_margin
            : "N/A",
        tone: strTone(row.gross_margin),
      })),
    },
    {
      label: "Operating Margin",
      cells: finRows.map((row) => {
        const v = ratio(row.operating_income, row.revenue);
        return { value: fmtPct(v), tone: numTone(v) };
      }),
    },
    {
      label: "Net Income Margin",
      cells: finRows.map((row) => {
        const v = ratio(row.net_income, row.revenue);
        return { value: fmtPct(v), tone: numTone(v) };
      }),
    },
    {
      label: "FCF Margin",
      cells: finRows.map((row) => {
        const v = ratio(row.fcf, row.revenue);
        return { value: fmtPct(v), tone: numTone(v) };
      }),
    },
  ];

  const priceUp = (profile?.change ?? 0) >= 0;

  return (
    <div className="flex min-h-0 w-full flex-1 overflow-hidden font-inter text-tikt-green">
      <Sidebar />

      <main className="flex-1 overflow-y-auto bg-tikt-cream">
        <div className="mx-auto w-full max-w-[1000px] px-8 pb-20 pt-8">
          {/* back */}
          <button
            type="button"
            onClick={() => navigate("/")}
            className="mb-7 inline-flex items-center gap-1.5 text-[13px] font-medium text-tikt-green/50 hover:text-tikt-green"
          >
            ← Back
          </button>

          {error ? (
            <div className="flex min-h-[50vh] items-center justify-center px-4 text-center">
              <p className="max-w-[480px] text-[15px] leading-[1.6] text-tikt-green/50">
                Could not load data for {symbol}. Please check the ticker and try
                again.
              </p>
            </div>
          ) : (
            <>
              {/* ───────────── COMPANY HEADER ───────────── */}
              <div className="flex items-start justify-between gap-6">
                <div className="flex items-center gap-4">
                  <CompanyLogo
                    key={symbol}
                    logo={profile?.logo}
                    name={profile?.name}
                    ticker={symbol}
                  />
                  <div>
                    <div className="text-[11px] font-semibold uppercase tracking-[2px] text-tikt-gold">
                      {profile?.exchange ? `${profile.exchange} · ${symbol}` : symbol}
                    </div>
                    <h1 className="mt-1.5 font-display text-[28px] font-bold leading-tight text-tikt-green">
                      {loading.profile ? (
                        <span className="text-tikt-green/50">Loading…</span>
                      ) : (
                        profile?.name || symbol
                      )}
                    </h1>
                    <div className="mt-1 text-[13px] text-tikt-green/50">
                      {loading.profile
                        ? ""
                        : [profile?.sector, profile?.industry]
                            .filter(Boolean)
                            .join(" · ") || "—"}
                    </div>
                  </div>
                </div>

                <div className="flex flex-col items-end gap-3">
                  <div className="text-right">
                    {loading.profile ? (
                      <div className="text-[14px] text-tikt-green/50">Loading…</div>
                    ) : (
                      <>
                        <div className="text-[30px] font-bold tabular-nums leading-none text-tikt-green">
                          {profile?.price != null
                            ? `$${profile.price.toFixed(2)}`
                            : "—"}
                        </div>
                        {profile?.change != null &&
                          profile?.change_pct != null && (
                            <div
                              className={`mt-1.5 text-[14px] font-semibold tabular-nums ${
                                priceUp ? "text-green-600" : "text-red-600"
                              }`}
                            >
                              {priceUp ? "▲" : "▼"} $
                              {Math.abs(profile.change).toFixed(2)} (
                              {profile.change_pct >= 0 ? "+" : ""}
                              {profile.change_pct.toFixed(2)}%)
                            </div>
                          )}
                      </>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() =>
                      navigate(`/debate/${symbol}`, {
                        state: {
                          company: profile?.name || symbol,
                          topic: `Is ${profile?.name || symbol} a good investment?`,
                          agents: [
                            "buffett",
                            "cathie_wood",
                            "peter_lynch",
                            "howard_marks",
                            "ray_dalio",
                          ],
                          turns: 2,
                        },
                      })
                    }
                    className="rounded-none bg-tikt-green px-5 py-2.5 text-[13px] font-semibold tracking-[0.3px] text-tikt-cream hover:bg-tikt-greenDark"
                  >
                    Start Debate →
                  </button>
                </div>
              </div>

              {/* ───────────── PRICE CHART ───────────── */}
              <div className="mt-8 rounded-lg border-[0.5px] border-tikt-green/15 bg-white p-5">
                <div className="mb-4 flex items-center justify-between">
                  <div className="text-[11px] font-semibold uppercase tracking-[2px] text-tikt-gold">
                    Price History
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {RANGES.map((r) => {
                      const active = r === timeRange;
                      return (
                        <button
                          key={r}
                          type="button"
                          onClick={() => setTimeRange(r)}
                          className={`rounded border px-2.5 py-1 text-[11px] font-semibold tracking-[0.5px] ${
                            active
                              ? "border-tikt-gold text-tikt-gold"
                              : "border-tikt-green/15 text-tikt-green/50 hover:text-tikt-green"
                          }`}
                        >
                          {r}
                        </button>
                      );
                    })}
                  </div>
                </div>

                <div className="h-[280px] w-full">
                  {loading.chart ? (
                    <div className="flex h-full items-center justify-center text-[13px] text-tikt-green/50">
                      Loading chart…
                    </div>
                  ) : chartData.length === 0 ? (
                    <div className="flex h-full items-center justify-center text-[13px] text-tikt-green/50">
                      No price data available.
                    </div>
                  ) : (
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart
                        data={chartData}
                        margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
                      >
                        <defs>
                          <linearGradient id="goldFill" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stopColor="#C9A84C" stopOpacity={0.18} />
                            <stop offset="100%" stopColor="#C9A84C" stopOpacity={0} />
                          </linearGradient>
                        </defs>
                        <CartesianGrid vertical={false} stroke="#1B4332" strokeOpacity={0.08} />
                        <XAxis dataKey="date" hide />
                        <YAxis hide domain={["dataMin - 10", "dataMax + 10"]} />
                        <Tooltip
                          cursor={{ stroke: "#C9A84C", strokeOpacity: 0.3 }}
                          contentStyle={{
                            background: "#1B4332",
                            border: "none",
                            borderRadius: 4,
                            color: "#FAF7F2",
                            fontSize: 12,
                          }}
                          labelStyle={{ color: "#C9A84C" }}
                          formatter={(value) => [`$${value}`, "Price"]}
                        />
                        <Area
                          type="monotone"
                          dataKey="price"
                          stroke="#C9A84C"
                          strokeWidth={2}
                          fill="url(#goldFill)"
                          dot={false}
                          activeDot={{ r: 4, fill: "#C9A84C", stroke: "#FAF7F2", strokeWidth: 2 }}
                        />
                      </AreaChart>
                    </ResponsiveContainer>
                  )}
                </div>
              </div>

              {/* ───────────── METRICS GRID ───────────── */}
              <div className="mt-6 grid grid-cols-4 gap-4">
                {METRIC_CARDS.map((c) => (
                  <div
                    key={c.key}
                    className="rounded-lg border-[0.5px] border-tikt-green/15 bg-white p-4"
                  >
                    <div className="mb-2 text-[11px] font-semibold uppercase tracking-[1px] text-tikt-green/50">
                      {c.label}
                    </div>
                    <div className="text-[20px] font-semibold tabular-nums text-tikt-green">
                      {loading.metrics ? "—" : metrics?.[c.key] ?? "—"}
                    </div>
                  </div>
                ))}
              </div>

              {/* ─────────── FINANCIAL SUMMARY + MARGINS (shared view toggle) ─────────── */}
              <div className="mt-6 mb-3 flex items-center justify-between">
                <div className="text-[11px] font-semibold uppercase tracking-[2px] text-tikt-gold">
                  Financial Summary
                </div>
                <button
                  type="button"
                  onClick={() => navigate(`/company/${symbol}/statements`)}
                  className="text-[12px] font-semibold text-tikt-gold hover:text-tikt-greenDark"
                >
                  View Full Statements →
                </button>
              </div>
              {/* shared Quarterly/Annual/TTM toggle — controls BOTH tables below */}
              <div className="mb-3 flex w-fit rounded border-[0.5px] border-tikt-green/15 p-0.5">
                {[
                  { k: "quarterly", label: "Quarterly" },
                  { k: "annual", label: "Annual" },
                  { k: "ttm", label: "TTM" },
                ].map((opt) => {
                  const active = financialView === opt.k;
                  return (
                    <button
                      key={opt.k}
                      type="button"
                      onClick={() => setFinancialView(opt.k)}
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

              {/* financial summary table card (view toggle moved out, above) */}
              <div className="overflow-hidden rounded-lg border-[0.5px] border-tikt-green/15 bg-white">

                <div className="overflow-x-auto">
                  {loading.financials ? (
                    <div className="px-6 py-10 text-center text-[13px] text-tikt-green/50">
                      Loading…
                    </div>
                  ) : finRows.length === 0 ? (
                    <div className="px-6 py-10 text-center text-[13px] text-tikt-green/50">
                      No {financialView} data available.
                    </div>
                  ) : (
                    <table className="w-full border-collapse">
                      <thead>
                        <tr className="border-b border-tikt-gold/20">
                          <th className="px-6 py-2.5 text-left text-[11px] font-semibold uppercase tracking-[1px] text-tikt-green/50">
                            Metric
                          </th>
                          {finRows.map((row, i) => (
                            <th
                              key={row.period}
                              className={`whitespace-nowrap px-4 py-2.5 text-right text-[11px] font-semibold uppercase tracking-[1px] ${
                                i === finRows.length - 1
                                  ? "text-tikt-gold"
                                  : "text-tikt-green/50"
                              }`}
                            >
                              {row.period}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {FIN_COLUMNS.map((c, i) => (
                          <tr
                            key={c.key}
                            className={i % 2 === 1 ? "bg-tikt-green/[0.02]" : ""}
                          >
                            <td className="whitespace-nowrap px-6 py-3 text-left text-[13px] font-semibold text-tikt-green">
                              {c.label}
                            </td>
                            {finRows.map((row) => (
                              <td
                                key={row.period}
                                className={`whitespace-nowrap px-4 py-3 text-right text-[13px] tabular-nums ${moneyTone(
                                  row[c.key]
                                )}`}
                              >
                                {row[c.key]}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </div>

              {/* ───────────── MARGINS (trend table) ───────────── */}
              <div className="mt-6">
                <div className="mb-3 text-[11px] font-semibold uppercase tracking-[2px] text-tikt-gold">
                  Margins
                </div>
                <div className="overflow-hidden rounded-lg border-[0.5px] border-tikt-green/15 bg-white">
                  <div className="overflow-x-auto">
                    {loading.financials ? (
                      <div className="px-6 py-10 text-center text-[13px] text-tikt-green/50">
                        Loading…
                      </div>
                    ) : finRows.length === 0 ? (
                      <div className="px-6 py-10 text-center text-[13px] text-tikt-green/50">
                        No margin data available.
                      </div>
                    ) : (
                      <table className="w-full border-collapse">
                        <thead>
                          <tr className="border-b border-tikt-gold/20">
                            <th className="px-6 py-2.5 text-left text-[11px] font-semibold uppercase tracking-[1px] text-tikt-green/50">
                              Metric
                            </th>
                            {finRows.map((row, i) => (
                              <th
                                key={row.period}
                                className={`whitespace-nowrap px-4 py-2.5 text-right text-[11px] font-semibold uppercase tracking-[1px] ${
                                  i === finRows.length - 1
                                    ? "text-tikt-gold"
                                    : "text-tikt-green/50"
                                }`}
                              >
                                {row.period}
                              </th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {marginRows.map((r, i) => (
                            <tr
                              key={r.label}
                              className={i % 2 === 1 ? "bg-tikt-green/[0.02]" : ""}
                            >
                              <td className="whitespace-nowrap px-6 py-3 text-left text-[13px] font-semibold text-tikt-green">
                                {r.label}
                              </td>
                              {r.cells.map((cell, j) => (
                                <td
                                  key={finRows[j].period}
                                  className={`whitespace-nowrap px-4 py-3 text-right text-[13px] tabular-nums ${
                                    loading.financials ? "text-tikt-green/50" : cell.tone
                                  }`}
                                >
                                  {loading.financials ? "—" : cell.value}
                                </td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>
              </div>

              {/* ───────────── DEBATE PANEL (full width) ───────────── */}
              <div className="mt-6 rounded-lg border-[0.5px] border-tikt-gold bg-white p-6">
                <div className="flex flex-col gap-6 md:flex-row md:items-stretch">
                  {/* left: tag, heading, subtext, agent list */}
                  <div className="flex-1">
                    <div className="inline-flex items-center rounded-full bg-tikt-gold/10 px-3 py-1 text-[10px] font-bold uppercase tracking-[1.5px] text-tikt-gold">
                      AI Debate Engine
                    </div>
                    <h2 className="mt-4 font-display text-[22px] font-bold leading-snug text-tikt-green">
                      Five legendary investors. One verdict.
                    </h2>
                    <p className="mt-2 max-w-[640px] text-[13px] leading-[1.6] text-tikt-green/50">
                      Buffett, Munger, Lynch, Burry and Wood each argue the bull and
                      bear case from their own philosophy — grounded in {symbol}&rsquo;s
                      actual filings — then a neutral analyst hands you the synthesis.
                    </p>

                    <div className="mt-5 flex flex-wrap gap-x-6 gap-y-2.5">
                      {AGENTS.map((a) => (
                        <div key={a} className="flex items-center gap-2.5">
                          <span className="h-1.5 w-1.5 rounded-full bg-tikt-gold" />
                          <span className="text-[13px] font-medium text-tikt-green">{a}</span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* right: tall full-height debate button */}
                  <div className="flex md:w-[240px] md:flex-shrink-0">
                    <button
                      type="button"
                      onClick={() =>
                        navigate(`/debate/${symbol}`, {
                          state: {
                            company: profile?.name || symbol,
                            topic: `Is ${profile?.name || symbol} a good investment?`,
                            agents: [
                              "buffett",
                              "cathie_wood",
                              "peter_lynch",
                              "howard_marks",
                              "ray_dalio",
                            ],
                            turns: 2,
                          },
                        })
                      }
                      className="h-full w-full rounded-none bg-tikt-green px-5 py-4 text-[14px] font-semibold tracking-[0.3px] text-tikt-cream hover:bg-tikt-greenDark"
                    >
                      Start Debate on {symbol} →
                    </button>
                  </div>
                </div>

                {/* transcript preview — full width */}
                <div className="mt-6 border-t-[0.5px] border-tikt-green/15 pt-4">
                  <div className="text-[10px] font-semibold uppercase tracking-[1.5px] text-tikt-green/50">
                    Last debate · 2 days ago
                  </div>
                  <div className="mt-3 flex flex-col gap-3">
                    {EXCERPTS.map((e) => (
                      <div key={e.agent} className="text-[12px] leading-[1.5]">
                        <span className="font-semibold text-tikt-gold">{e.agent}: </span>
                        <span className="italic text-tikt-green/50">&ldquo;{e.quote}&rdquo;</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      </main>
    </div>
  );
}
