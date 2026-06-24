import { BrowserRouter, Routes, Route } from "react-router-dom";
import MarketTicker from "./components/MarketTicker";
import Home from "./pages/Home";
import Company from "./pages/Company";

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen flex-col overflow-hidden">
        {/* MarketTicker lives outside Routes so it persists across every page */}
        <MarketTicker />
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/company/:ticker" element={<Company />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}
