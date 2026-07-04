import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { apiFetch, getToken, setSession } from "../lib/api";

// Login.jsx — /login. The front door: a single password field. Each member's
// password doubles as their API token; /auth/verify swaps it for the user
// profile stored alongside the token in localStorage. Same editorial cream
// look as the rest of TIKT.

export default function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Where the guard bounced the user from (default: home).
  const from = location.state?.from
    ? location.state.from.pathname + (location.state.from.search || "")
    : "/";

  // Already logged in — skip the form entirely.
  if (getToken()) return <Navigate to={from} replace />;

  async function handleSubmit(e) {
    e.preventDefault();
    if (!password.trim() || busy) return;
    setBusy(true);
    setError("");
    try {
      const res = await apiFetch("/auth/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: password.trim() }),
      });
      if (res.status === 401) {
        setError("Invalid password");
        return;
      }
      if (!res.ok) throw new Error(`Verify failed (${res.status})`);
      const data = await res.json();
      setSession(data.token, data.user);
      navigate(from, { replace: true });
    } catch {
      setError("Could not reach the server. Try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-tikt-cream px-6">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-[340px] rounded-lg border border-tikt-border bg-tikt-panel p-8 shadow-[0_1px_2px_rgba(64,52,24,0.05)]"
      >
        {/* logo — same 3-bar mark as the sidebar */}
        <div className="mb-8 flex flex-col items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-[6px] bg-tikt-green">
            <div className="flex items-end gap-[3px]">
              <div className="h-[9px] w-[3.5px] rounded-[1px] bg-tikt-cream" />
              <div className="h-[15px] w-[3.5px] rounded-[1px] bg-tikt-gold" />
              <div className="h-[11px] w-[3.5px] rounded-[1px] bg-tikt-cream" />
            </div>
          </div>
          <div className="font-serif text-[26px] font-bold tracking-[4px] text-tikt-ink">
            TIKT
          </div>
          <div className="text-[11px] uppercase tracking-[2px] text-tikt-faint">
            Investors of the Kitchen Table
          </div>
        </div>

        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          aria-label="Password"
          autoFocus
          className="w-full rounded-md border border-tikt-border bg-tikt-panel px-4 py-3 text-[14px] text-tikt-ink outline-none placeholder:text-tikt-faint focus:border-tikt-green"
        />
        {error && (
          <div className="mt-2 text-[12.5px] text-tikt-neg">{error}</div>
        )}
        <button
          type="submit"
          disabled={busy}
          className="mt-4 w-full rounded-md bg-tikt-green py-3 text-[14px] font-semibold text-tikt-cream hover:bg-tikt-greenDark disabled:opacity-60"
        >
          {busy ? "Checking…" : "Enter the Table"}
        </button>
      </form>
    </div>
  );
}
