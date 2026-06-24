# TIKT Backend (FastAPI)

Minimal API skeleton for the TIKT platform.

## Run

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Serves on http://localhost:8000

## Endpoints

- `GET /health` → `{"status": "ok"}`
- `GET /market-data` → placeholder global index data (matches the frontend ticker bar)
- `POST /debate` → `{"message": "not implemented"}` (placeholder)

## TODO

Import the project-root debate engine (`main.py`) to power `/debate` with the
live multi-agent debate.
