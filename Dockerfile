# ==============================================================================
#  Dockerfile — The Kitchen Table (Railway backend + debate engine)
#
#  Railway was previously deploying only the backend/ folder (Root Directory =
#  "backend"), so the debate engine (root main.py), config.py, scripts/, and
#  agents/ were never in the container — every "Run Debate" failed because the
#  backend loads those files at runtime (see backend/main.py:_load_debate_engine).
#
#  This image deploys the WHOLE repo so those files are importable, but installs
#  only the slim runtime deps in backend/requirements.txt — NOT the heavy dev set
#  in the root requirements.txt (chromadb, torch/whisper, umap, plotly, yt-dlp).
#
#  To use it: in the Railway service Settings, clear "Root Directory" (set it to
#  blank / "/") so the build context is the repo root, and Railway will pick up
#  this Dockerfile automatically.
# ==============================================================================
FROM python:3.11-slim

# System libraries WeasyPrint needs to render PDFs (mirrors nixpacks.toml:
# pango, cairo, gdk-pixbuf, libxml2). fonts-dejavu-core gives it text glyphs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        libxml2 \
        shared-mime-info \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for better layer caching. Only the slim backend set
# (now includes the engine's runtime deps: anthropic, langgraph, requests).
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy the whole project so the engine + config + scripts + agents are present
# and importable by backend/main.py at runtime.
COPY . .

# Railway injects $PORT at runtime; default to 8000 for a local `docker run`.
ENV PORT=8000
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]
