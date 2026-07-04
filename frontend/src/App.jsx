import { BrowserRouter, Routes, Route } from "react-router-dom";
import RequireAuth from "./components/RequireAuth";
import Login from "./pages/Login";
import Home from "./pages/Home";
import Company from "./pages/Company";
import Debate from "./pages/Debate";
import History from "./pages/History";
import Statements from "./pages/Statements";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Public front door */}
        <Route path="/login" element={<Login />} />
        {/* Everything else requires a stored token; RequireAuth also owns the
            app chrome (MarketTicker + layout) so /login stays clean. */}
        <Route element={<RequireAuth />}>
          <Route path="/" element={<Home />} />
          <Route path="/company/:ticker" element={<Company />} />
          <Route path="/company/:ticker/statements" element={<Statements />} />
          <Route path="/debate/:ticker" element={<Debate />} />
          <Route path="/history" element={<History />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
