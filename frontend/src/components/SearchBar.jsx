import { useEffect, useRef, useState } from "react";

// SearchBar.jsx — the Home hero search from the TIKT design: a wide input with
// a magnifier icon on the left and a green "Research →" button inside on the
// right. Submits on Enter or the button click, calling onSearch(query).
//
// It also shows an FMP-powered autocomplete dropdown: as the user types (>= 2
// chars, debounced 300ms) it hits the backend /search proxy and lists matching
// tickers. Selecting a result (click, or Enter on a highlighted row) calls
// onSearch with that symbol; plain Enter / the button submit the typed text.

const API = "https://investors-of-the-kitchen-table-production.up.railway.app";

export default function SearchBar({ onSearch }) {
  const [inputValue, setInputValue] = useState("");
  const [results, setResults] = useState([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [loading, setLoading] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);

  const debounceRef = useRef(null);
  const containerRef = useRef(null);

  // Close the dropdown on any click outside the search container.
  useEffect(() => {
    const onDocMouseDown = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, []);

  // Clear any pending debounce timer on unmount.
  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    []
  );

  const handleChange = (e) => {
    const val = e.target.value;
    setInputValue(val);
    setHighlightedIndex(-1);
    if (debounceRef.current) clearTimeout(debounceRef.current);

    const q = val.trim();
    if (q.length < 2) {
      setResults([]);
      setShowDropdown(false);
      setLoading(false);
      return;
    }

    setLoading(true);
    setShowDropdown(true);
    debounceRef.current = setTimeout(() => {
      fetch(`${API}/search?query=${encodeURIComponent(q)}`)
        .then((r) => r.json())
        .then((d) => {
          setResults(Array.isArray(d) ? d : []);
          setLoading(false);
          setShowDropdown(true);
        })
        .catch(() => {
          setResults([]);
          setLoading(false);
        });
    }, 300);
  };

  const submit = () => {
    const value = inputValue.trim();
    if (value) {
      onSearch?.(value);
      setShowDropdown(false);
    }
  };

  const selectResult = (symbol) => {
    if (!symbol) return;
    onSearch?.(symbol);
    setShowDropdown(false);
    setHighlightedIndex(-1);
  };

  const onKeyDown = (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (results.length) {
        setShowDropdown(true);
        setHighlightedIndex((i) => (i + 1) % results.length);
      }
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (results.length) {
        setHighlightedIndex((i) => (i <= 0 ? results.length - 1 : i - 1));
      }
    } else if (e.key === "Enter") {
      if (showDropdown && highlightedIndex >= 0 && results[highlightedIndex]) {
        selectResult(results[highlightedIndex].symbol);
      } else {
        submit();
      }
    } else if (e.key === "Escape") {
      setShowDropdown(false);
      setHighlightedIndex(-1);
    }
  };

  return (
    <div
      ref={containerRef}
      className="relative w-full max-w-[650px] rounded-lg shadow-[0_4px_24px_rgba(64,52,24,0.07)]"
    >
      <div className="pointer-events-none absolute left-[21px] top-1/2 h-4 w-4 -translate-y-1/2 rounded-full border-2 border-tikt-faint" />
      <div
        className="pointer-events-none absolute left-[33px] h-[2px] w-[9px] origin-left rotate-45 bg-tikt-faint"
        style={{ top: "calc(50% + 6px)" }}
      />
      <input
        value={inputValue}
        onChange={handleChange}
        onKeyDown={onKeyDown}
        onFocus={() => {
          if (results.length) setShowDropdown(true);
        }}
        placeholder="Search a company or ticker — try NVDA"
        aria-label="Search a company or ticker"
        autoComplete="off"
        className="w-full rounded-lg border border-tikt-border bg-tikt-panel py-[21px] pl-[51px] pr-[158px] text-base text-tikt-ink outline-none focus:border-tikt-green"
      />
      <button
        type="button"
        onClick={submit}
        className="absolute right-[9px] top-1/2 -translate-y-1/2 whitespace-nowrap rounded-md bg-tikt-green px-6 py-[13px] text-[15px] font-semibold tracking-[0.2px] text-tikt-cream hover:bg-tikt-greenDark"
      >
        Research →
      </button>

      {/* autocomplete dropdown */}
      {showDropdown && (
        <div className="absolute left-0 right-0 top-full z-50 mt-2 overflow-hidden rounded-lg border border-tikt-border bg-white shadow-[0_8px_24px_rgba(64,52,24,0.12)]">
          {loading ? (
            <div className="px-4 py-3 text-left text-[13px] text-tikt-faint">
              Searching…
            </div>
          ) : results.length === 0 ? (
            <div className="px-4 py-3 text-left text-[13px] text-tikt-faint">
              No results found
            </div>
          ) : (
            results.slice(0, 8).map((r, i) => (
              <button
                key={`${r.symbol}-${i}`}
                type="button"
                onClick={() => selectResult(r.symbol)}
                onMouseEnter={() => setHighlightedIndex(i)}
                className={`flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left ${
                  i === highlightedIndex ? "bg-tikt-green/[0.05]" : ""
                }`}
              >
                <span className="flex min-w-0 items-baseline gap-2">
                  <span className="text-[14px] font-bold text-tikt-gold">
                    {r.symbol}
                  </span>
                  <span className="truncate text-[13px] text-tikt-green">
                    {r.name}
                  </span>
                </span>
                <span className="shrink-0 text-[11px] text-tikt-faint">
                  {r.exchange}
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
