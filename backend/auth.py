"""Per-user Bearer-token auth. Each user's key is both their password and their
API token (token == password == env var value). Keys live in env vars — the
Railway dashboard in prod, the repo-root .env locally — so they rotate without
code changes. An unset/empty key disables that user; "" must never match."""

import os

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_USERS = [
    ("PECKHAM_KEY", {"owner": "peckham", "name": "Peckham Alun", "initials": "PA", "colour": "#1B4332"}),
    ("DARIUS_KEY",  {"owner": "darius",  "name": "Darius",       "initials": "D",  "colour": "#C9A84C"}),
    ("ROYDEN_KEY",  {"owner": "royden",  "name": "Royden",       "initials": "R",  "colour": "#23543F"}),
]


def _token_map() -> dict[str, dict]:
    # Built lazily per call (trivial cost, 3 users) so it works regardless of
    # when load_dotenv() runs relative to this module's import, and so key
    # rotation only needs a process restart, never a code change.
    tokens = {}
    for env_name, user in _USERS:
        key = (os.getenv(env_name) or "").strip()
        if key:  # skip empty/unset — never map "" to a user
            tokens[key] = user
    return tokens


def verify_token(token: str) -> dict | None:
    """Return the user dict for a valid token, else None."""
    return _token_map().get(token) if token else None


_bearer = HTTPBearer(auto_error=False)  # auto_error=False -> our own 401, not 403


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    user = verify_token(credentials.credentials if credentials else "")
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or missing token")
    return user
