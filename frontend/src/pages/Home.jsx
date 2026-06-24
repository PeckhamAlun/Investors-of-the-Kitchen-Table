import { useNavigate } from "react-router-dom";
import Sidebar from "../components/TickerBar";
import SearchBar from "../components/SearchBar";

// Home.jsx — TIKT Editorial home, a faithful recreation of the design file:
// a 256px left sidebar + a scrollable main area (sticky top bar, hero badge,
// serif headline, hero search, trending pills, and the five-analyst cards).

const TRENDING = ["NVDA", "MSFT", "TSLA", "MDB"];

const INVESTORS = [
  { initials: "WB", name: "Warren Buffett", title: "Value · Berkshire" },
  { initials: "CM", name: "Charlie Munger", title: "Mental models" },
  { initials: "PL", name: "Peter Lynch", title: "Growth at a price" },
  { initials: "MB", name: "Michael Burry", title: "Deep-value contrarian" },
  { initials: "CW", name: "Cathie Wood", title: "Disruptive growth" },
];

export default function Home() {
  const navigate = useNavigate();

  const goToCompany = (ticker) => {
    const symbol = ticker.trim().toUpperCase();
    if (symbol) navigate(`/company/${encodeURIComponent(symbol)}`);
  };

  return (
    <div className="flex min-h-0 w-full flex-1 overflow-hidden font-sans text-tikt-ink">
      <Sidebar />

      <main className="flex-1 overflow-y-auto bg-tikt-cream">
        {/* sticky top bar */}
        <div className="sticky top-0 z-20 flex h-[62px] items-center justify-between border-b border-tikt-border bg-[rgba(250,247,242,0.9)] px-[38px] backdrop-blur-[8px]">
          <div className="text-[12px] font-semibold tracking-[1.5px] text-tikt-faint">
            HOME
          </div>
          <div className="flex gap-2.5">
            {[
              { k: "S&P 500", v: "+0.54%" },
              { k: "Nasdaq", v: "+0.59%" },
            ].map((m) => (
              <div
                key={m.k}
                className="flex min-w-[98px] flex-col rounded-md border border-tikt-border bg-tikt-panel px-[15px] py-1.5"
              >
                <span className="text-[10.5px] font-semibold tracking-[0.5px] text-tikt-faint">
                  {m.k}
                </span>
                <span className="text-[13px] font-semibold tabular-nums text-tikt-pos">
                  {m.v}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* home content */}
        <div className="mx-auto flex max-w-[920px] flex-col items-center px-8 pb-20 pt-[100px]">
          {/* badge */}
          <div className="mb-8 inline-flex items-center gap-[9px] rounded-full border border-[rgba(27,67,50,0.18)] bg-[rgba(27,67,50,0.06)] px-4 py-1.5">
            <div className="h-1.5 w-1.5 rounded-full bg-tikt-gold" />
            <span className="text-[12px] font-bold uppercase tracking-[1.2px] text-tikt-green">
              AI-powered equity research
            </span>
          </div>

          {/* headline */}
          <h1 className="mb-[22px] text-center font-serif text-[56px] font-semibold leading-[1.08] tracking-[-0.5px] text-tikt-ink [text-wrap:balance]">
            Five legendary minds.
            <br />
            One stock. <span className="italic text-tikt-green">Live debate.</span>
          </h1>
          <p className="mb-[42px] max-w-[590px] text-center text-[18px] leading-[1.6] text-tikt-muted [text-wrap:pretty]">
            Drop in any ticker and watch Buffett, Munger, Lynch, Burry and Wood
            argue the bull and bear case — then hand you the synthesis.
          </p>

          {/* hero search */}
          <SearchBar onSearch={goToCompany} />

          {/* trending */}
          <div className="mt-[22px] flex items-center gap-2">
            <span className="text-[12px] font-semibold tracking-[1px] text-tikt-faint">
              TRENDING
            </span>
            {TRENDING.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => goToCompany(t)}
                className="cursor-pointer rounded-full border border-tikt-border bg-tikt-panel px-[14px] py-1.5 text-[13px] font-semibold tracking-[0.5px] text-tikt-body hover:border-tikt-green hover:text-tikt-green"
              >
                {t}
              </button>
            ))}
          </div>

          {/* the table */}
          <div className="mt-20 w-full">
            <div className="mb-[30px] flex items-center justify-center gap-4">
              <div className="h-px max-w-[130px] flex-1 bg-[linear-gradient(90deg,transparent,rgba(201,168,76,0.55))]" />
              <span className="font-serif text-[15px] italic text-tikt-muted">
                The Table · Five Analysts
              </span>
              <div className="h-px max-w-[130px] flex-1 bg-[linear-gradient(90deg,rgba(201,168,76,0.55),transparent)]" />
            </div>
            <div className="grid grid-cols-5 gap-4">
              {INVESTORS.map((inv) => (
                <div
                  key={inv.initials}
                  className="flex flex-col items-center gap-3 rounded-lg border border-tikt-border bg-tikt-panel px-[14px] py-[22px] text-center shadow-[0_1px_3px_rgba(64,52,24,0.05)] hover:border-tikt-gold hover:shadow-[0_8px_22px_rgba(64,52,24,0.08)]"
                >
                  <div className="flex h-[54px] w-[54px] items-center justify-center rounded-md border border-[rgba(27,67,50,0.3)] bg-tikt-tan font-serif text-[18px] font-semibold text-tikt-green">
                    {inv.initials}
                  </div>
                  <div className="font-serif text-[15px] font-semibold leading-[1.2] text-tikt-ink">
                    {inv.name}
                  </div>
                  <div className="text-[11.5px] leading-[1.35] tracking-[0.2px] text-tikt-faint">
                    {inv.title}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
