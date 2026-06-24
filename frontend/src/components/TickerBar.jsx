// TickerBar.jsx
// NOTE: the TIKT design reference has no ticker bar — it has a left sidebar.
// This file now renders that sidebar (logo, search, nav, recents, user). The
// filename is kept so existing imports keep resolving; it is imported as
// `Sidebar` in Home.jsx.

import { useNavigate } from "react-router-dom";

function IconResearch() {
  return (
    <div className="flex w-4 items-end justify-center gap-[2.5px]">
      <div className="h-[7px] w-[3px] bg-tikt-green" />
      <div className="h-[12px] w-[3px] bg-tikt-green" />
      <div className="h-[9px] w-[3px] bg-tikt-green" />
    </div>
  );
}

function NavItem({ icon, label, active }) {
  if (active) {
    return (
      <div className="flex cursor-pointer items-center gap-3 rounded-md border-l-2 border-tikt-green bg-tikt-panel px-3 py-2.5 text-sm font-semibold text-tikt-green shadow-[0_1px_2px_rgba(64,52,24,0.05)]">
        {icon}
        {label}
      </div>
    );
  }
  return (
    <div className="flex cursor-pointer items-center gap-3 rounded-md border-l-2 border-transparent px-3 py-2.5 text-sm font-medium text-tikt-muted hover:bg-tikt-hover hover:text-tikt-ink">
      {icon}
      {label}
    </div>
  );
}

const NAV = [
  { label: "Research", active: true, icon: <IconResearch /> },
  {
    label: "Watchlist",
    icon: (
      <div className="flex w-4 justify-center">
        <div className="h-[11px] w-[11px] rounded-[2px] border-[1.6px] border-current" />
      </div>
    ),
  },
  {
    label: "Debates",
    icon: (
      <div className="relative flex w-4 justify-center">
        <div className="h-[9px] w-[9px] rounded-full border-[1.6px] border-current" />
        <div className="ml-[-4px] mt-1 h-[9px] w-[9px] rounded-full border-[1.6px] border-current" />
      </div>
    ),
  },
  {
    label: "Charts",
    icon: (
      <div className="flex w-4 justify-center">
        <div className="h-[11px] w-[13px] border-b-[1.6px] border-l-[1.6px] border-current" />
      </div>
    ),
  },
  {
    label: "History",
    icon: (
      <div className="flex w-4 justify-center">
        <div className="h-[11px] w-[11px] rounded-full border-[1.6px] border-current" />
      </div>
    ),
  },
];

const RECENT = [
  { sym: "NVDA", chg: "+2.49%", up: true },
  { sym: "MDB", chg: "-3.28%", up: false },
  { sym: "TSLA", chg: "+1.04%", up: true },
];

export default function Sidebar() {
  const navigate = useNavigate();

  return (
    <aside className="flex w-64 flex-shrink-0 flex-col border-r border-tikt-border bg-tikt-sand px-[18px] py-6 text-tikt-body">
      {/* logo */}
      <div className="flex cursor-pointer items-center gap-3 pb-5 pl-1.5 pr-1.5 pt-[2px]">
        <div className="flex h-8 w-8 items-center justify-center rounded-[5px] bg-tikt-green">
          <div className="flex items-end gap-[2.5px]">
            <div className="h-[7px] w-[3px] rounded-[1px] bg-tikt-cream" />
            <div className="h-[12px] w-[3px] rounded-[1px] bg-tikt-gold" />
            <div className="h-[9px] w-[3px] rounded-[1px] bg-tikt-cream" />
          </div>
        </div>
        <div className="font-serif text-[21px] font-bold tracking-[3px] text-tikt-ink">
          TIKT
        </div>
      </div>

      {/* gold divider */}
      <div className="mb-[22px] mx-0.5 h-px bg-[linear-gradient(90deg,transparent,rgba(201,168,76,0.55),transparent)]" />

      {/* sidebar search */}
      <div className="relative mb-6">
        <div className="absolute left-[13px] top-1/2 h-3 w-3 -translate-y-1/2 rounded-full border-[1.6px] border-tikt-faint" />
        <div
          className="absolute left-[22px] h-[1.6px] w-[7px] origin-left rotate-45 bg-tikt-faint"
          style={{ top: "calc(50% + 5px)" }}
        />
        <input
          placeholder="Search any ticker…"
          aria-label="Search any ticker"
          className="w-full rounded-md border border-tikt-border bg-tikt-panel py-2.5 pl-[34px] pr-3 text-[13.5px] text-tikt-ink outline-none focus:border-tikt-green"
        />
      </div>

      {/* nav */}
      <nav className="flex flex-col gap-0.5">
        {NAV.map((n) => (
          <NavItem key={n.label} icon={n.icon} label={n.label} active={n.active} />
        ))}
      </nav>

      {/* recent */}
      <div className="mx-3 mb-3 mt-[26px] text-[10.5px] font-bold uppercase tracking-[1.5px] text-tikt-faint">
        Recent
      </div>
      <div className="flex flex-col gap-px">
        {RECENT.map((r) => (
          <button
            key={r.sym}
            type="button"
            onClick={() => navigate(`/company/${encodeURIComponent(r.sym)}`)}
            className="flex w-full cursor-pointer items-center justify-between rounded-md px-3 py-2 text-left text-[13px] text-tikt-body hover:bg-tikt-hover"
          >
            <span className="font-semibold tracking-[0.5px]">{r.sym}</span>
            <span
              className={`text-[12.5px] font-semibold tabular-nums ${
                r.up ? "text-tikt-pos" : "text-tikt-neg"
              }`}
            >
              {r.chg}
            </span>
          </button>
        ))}
      </div>

      {/* user */}
      <div className="mt-auto flex items-center gap-3 border-t border-tikt-border pb-1 pl-1.5 pr-1.5 pt-4">
        <div className="flex h-9 w-9 items-center justify-center rounded-md bg-tikt-green font-serif text-[13px] font-semibold text-tikt-cream">
          PA
        </div>
        <div className="leading-[1.3]">
          <div className="text-[13.5px] font-semibold text-tikt-ink">Peckham Alun</div>
          <div className="text-[11px] tracking-[0.3px] text-tikt-faint">Pro · Annual</div>
        </div>
      </div>
    </aside>
  );
}
