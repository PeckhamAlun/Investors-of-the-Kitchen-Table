import { Navigate, Outlet, useLocation } from "react-router-dom";
import { getToken } from "../lib/api";
import MarketTicker from "./MarketTicker";

// Layout route: gates every page behind a stored token and owns the app chrome
// (MarketTicker + flex column) so /login renders a clean standalone screen.
export default function RequireAuth() {
  const location = useLocation();
  if (!getToken()) {
    return <Navigate to="/login" replace state={{ from: location }} />;
  }
  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <MarketTicker />
      <Outlet />
    </div>
  );
}
