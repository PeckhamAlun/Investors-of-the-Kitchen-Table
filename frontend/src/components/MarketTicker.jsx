// MarketTicker.jsx
// Global markets ticker — a thin, full-width auto-scrolling marquee that sits at
// the very top of the app, above the sidebar and main content. Gold/cream text
// on a near-black green bar with a thin gold bottom border.
//
// TODO: wire to a live market-data endpoint. For now the price/change columns
// are hardcoded em-dash placeholders.

const INDICES = [
  { name: "S&P 500", price: "—", change: "—" },
  { name: "NASDAQ", price: "—", change: "—" },
  { name: "FTSE 100", price: "—", change: "—" },
  { name: "Nikkei 225", price: "—", change: "—" },
  { name: "DAX", price: "—", change: "—" },
  { name: "Hang Seng", price: "—", change: "—" },
  { name: "VIX", price: "—", change: "—" },
  { name: "10Y Treasury", price: "—", change: "—" },
];

function TickerItem({ name, price, change }) {
  return (
    <span className="mx-6 inline-flex items-center gap-2 whitespace-nowrap">
      <span className="text-tikt-gold">{name}</span>
      <span className="text-tikt-cream/70">{price}</span>
      <span className="text-tikt-gold/55">{change}</span>
    </span>
  );
}

export default function MarketTicker() {
  // Render the list twice so the marquee (translateX -50%) loops seamlessly.
  const loop = [...INDICES, ...INDICES];

  return (
    <div className="w-full overflow-hidden border-b border-tikt-gold/30 bg-tikt-dark py-1.5 text-[11px] font-normal leading-none text-tikt-cream">
      <div className="flex w-max animate-marquee">
        {loop.map((idx, i) => (
          <TickerItem key={`${idx.name}-${i}`} {...idx} />
        ))}
      </div>
    </div>
  );
}
