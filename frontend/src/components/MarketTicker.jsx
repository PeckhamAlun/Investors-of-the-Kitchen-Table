// MarketTicker.jsx
// Global markets ticker — a thin, full-width auto-scrolling marquee that sits at
// the very top of the app, above the sidebar and main content. Gold/cream text
// on a near-black green bar with a thin gold bottom border.
//
// On mount it fetches the live /market-data endpoint and swaps the hardcoded
// em-dash placeholders for real index prices and changes. The placeholders keep
// the bar rendered immediately — no loading state; real values swap in on arrival.

import { useEffect, useState } from "react";

const API = "https://investors-of-the-kitchen-table-production.up.railway.app";

// Hardcoded placeholders — shown immediately on first paint, then replaced by the
// live /market-data response. Shape matches the endpoint: { name, price, change }.
const PLACEHOLDERS = [
  { name: "S&P 500", price: "—", change: "—" },
  { name: "NASDAQ", price: "—", change: "—" },
  { name: "FTSE 100", price: "—", change: "—" },
  { name: "Nikkei 225", price: "—", change: "—" },
  { name: "DAX", price: "—", change: "—" },
  { name: "Hang Seng", price: "—", change: "—" },
  { name: "VIX", price: "—", change: "—" },
  { name: "10Y Treasury", price: "—", change: "—" },
];

// The change cell label. The live endpoint sends change_pct (a number, or a
// preformatted string) alongside a `positive` flag; placeholders carry only a
// plain `change` string. Fall back to `change` when no percent is present.
function changeLabel(item) {
  const pct = item.change_pct;
  if (pct === null || pct === undefined || pct === "") {
    return item.change ?? "—";
  }
  if (typeof pct === "number") {
    return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
  }
  return String(pct);
}

// Colour the change cell: green when positive, red when negative, muted gold for
// the em-dash placeholders (where `positive` is absent).
function changeTone(item) {
  if (item.positive === true) return "text-green-400";
  if (item.positive === false) return "text-red-400";
  return "text-tikt-gold/55";
}

function TickerItem({ item }) {
  return (
    <span className="mx-6 inline-flex items-center gap-2 whitespace-nowrap">
      <span className="text-tikt-gold">{item.name}</span>
      <span className="text-tikt-cream/70">{item.price}</span>
      <span className={changeTone(item)}>{changeLabel(item)}</span>
    </span>
  );
}

export default function MarketTicker() {
  // Default to the placeholders so the bar renders immediately; the fetch below
  // swaps in real values once they arrive.
  const [marketData, setMarketData] = useState(PLACEHOLDERS);

  // Fetch live market data once on mount. On any failure (network/parse) the
  // placeholders simply stay in place — there is no separate loading state.
  useEffect(() => {
    fetch(`${API}/market-data`)
      .then((r) => r.json())
      .then((d) => {
        if (Array.isArray(d) && d.length > 0) {
          setMarketData(d);
        }
      })
      .catch(() => {});
  }, []);

  // Render the list twice so the marquee (translateX -50%) loops seamlessly.
  const loop = [...marketData, ...marketData];

  return (
    <div className="w-full overflow-hidden border-b border-tikt-gold/30 bg-tikt-dark py-1.5 text-[11px] font-normal leading-none text-tikt-cream">
      <div className="flex w-max animate-marquee">
        {loop.map((idx, i) => (
          <TickerItem key={`${idx.name}-${i}`} item={idx} />
        ))}
      </div>
    </div>
  );
}
