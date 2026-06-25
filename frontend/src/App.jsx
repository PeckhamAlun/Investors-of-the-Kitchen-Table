import { BrowserRouter, Routes, Route } from "react-router-dom";
import MarketTicker from "./components/MarketTicker";
import Home from "./pages/Home";
import Company from "./pages/Company";
import Debate from "./pages/Debate";
import History from "./pages/History";
import Statements from "./pages/Statements";

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen flex-col overflow-hidden">
        {/* MarketTicker lives outside Routes so it persists across every page */}
        <MarketTicker />
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/company/:ticker" element={<Company />} />
          <Route path="/company/:ticker/statements" element={<Statements />} />
          <Route path="/debate/:ticker" element={<Debate />} />
          <Route path="/history" element={<History />} />
        </Routes>
      </div>
    </BrowserRouter>
  );
}
