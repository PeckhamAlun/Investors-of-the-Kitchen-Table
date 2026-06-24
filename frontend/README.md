# TIKT Frontend (Vite + React + Tailwind)

Home page for the TIKT platform — global markets ticker bar + ticker search.

## Run

```bash
npm install
npm run dev
```

Serves on http://localhost:5173

## Structure

- `src/pages/Home.jsx` — landing page (TIKT wordmark, tagline, search)
- `src/components/TickerBar.jsx` — auto-scrolling global index marquee
- `src/components/SearchBar.jsx` — ticker / company search input

## Palette (Tailwind custom colours)

- `tikt-navy` `#0F1B2D` · `tikt-card` `#1A2B3C` · `tikt-gold` `#C9A84C`

## TODO

Wire TickerBar and the search to the FastAPI backend (`/market-data`, `/debate`).
