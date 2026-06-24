import { BrowserRouter, Routes, Route } from "react-router-dom";
import MarketTicker from "./components/MarketTicker";
import Home from "./pages/Home";
import Company from "./pages/Company";
import Debate from "./pages/Debate";

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen flex-col overflow-hidden">
        {/* MarketTicker lives outside Routes so it persists across every page */}
        <MarketTicker />
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/company/:ticker" element={<Company />} />
          <Route path="/debate/:ticker" element={<Debate />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}
