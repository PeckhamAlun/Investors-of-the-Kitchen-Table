// Single fetch wrapper for every TIKT API call.
// - Base URL from VITE_API_URL (frontend/.env.local in dev, Vercel env in prod).
// - Adds Authorization: Bearer <tikt_token> when a token is stored.
// - Returns the RAW Response (two callers stream via response.body.getReader()).
// - Never sets Content-Type itself: FormData bodies need the browser's multipart
//   boundary; JSON callers pass their own header, which is preserved.

const BASE = import.meta.env.VITE_API_URL ?? "";
if (!BASE) {
  console.warn("VITE_API_URL is not set — API calls will hit the frontend origin.");
}

// For the rare non-fetch use (e.g. window.location.href downloads).
export const API_BASE = BASE;

export const TOKEN_KEY = "tikt_token";
export const USER_KEY = "tikt_user";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function getUser() {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null; // corrupted JSON — treat as logged out, never crash the UI
  }
}

export function setSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function apiFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const token = getToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  // Spread preserves method, body, and signal (AbortController) untouched.
  return fetch(`${BASE}${path}`, { ...options, headers });
}
