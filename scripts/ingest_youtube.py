"""
==============================================================================
  INGEST_YOUTUBE.PY — YOUTUBE → PHILOSOPHY RAG BRAIN (MongoDB Atlas + Gemini)
  Pulls a YouTube video's transcript and ingests it into an agent's MongoDB
  Atlas philosophy collection using Gemini embeddings. Two-tier transcript
  pipeline (identical to scripts/ingest_youtube.py):

      Tier 1  →  youtube-transcript-api  (free, instant — uses creator captions)
      Tier 2  →  yt-dlp audio + Whisper large-v3  (fallback when no captions)

  This is the MongoDB/Gemini counterpart of scripts/ingest_youtube.py. The
  yt-dlp / Whisper / caption-fetching / chunking logic is byte-for-byte the
  same; only the embedding model (Gemini gemini-embedding-001, 3072-dim instead
  of all-MiniLM-L6-v2) and the store (MongoDB Atlas instead of ChromaDB) change.

  All configuration is imported from config.py — nothing is hardcoded here.
==============================================================================

  USAGE:

  Single video:
      py -3.11 scripts/ingest_youtube.py --agent buffett --url "https://youtube.com/watch?v=..."

  Bulk (one URL per line, '#' for comments):
      py -3.11 scripts/ingest_youtube.py --agent buffett --bulk agents/buffett/urls.txt

  INSTALL:
      py -3.11 -m pip install youtube-transcript-api yt-dlp openai-whisper
      (pymongo, google-genai, langchain already installed)

  NOTES:
      - Tier 2 (Whisper) requires ffmpeg on PATH.
      - Temp audio is deleted immediately after transcription.
      - Re-ingesting a URL already in the collection is detected and skipped.

==============================================================================
"""

import os
import re
import sys
import time
import argparse
import warnings
from datetime import datetime, timezone
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from google import genai
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi

# ==============================================================================
# CONFIG — single source of truth (config.py)
# ==============================================================================

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from config import (
    MONGODB_URI, MONGODB_DB_NAME,
    GEMINI_EMBED_MODEL, GOOGLE_API_KEY,
    CHUNK_SIZE, CHUNK_OVERLAP,
    mongo_philosophy_collection, WHISPER_MODEL, WHISPER_LANGUAGE,
)

# Harden stdout so emoji / UTF-8 prints never raise under a non-UTF-8 console.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Whisper warns "FP16 is not supported on CPU; using FP32 instead" on every CPU
# transcription. Harmless noise on a CPU box — silence it.
warnings.filterwarnings("ignore", message="FP16 is not supported on CPU")


# ==============================================================================
# EMBEDDING / STORE BATCHING
# ==============================================================================

EMBED_BATCH_SIZE = 50    # chunks per Gemini embedding + insert batch
BATCH_SLEEP_SECS = 0.5   # pause between batches to avoid rate limits


# ==============================================================================
# YT-DLP AUTH — browser cookies to satisfy YouTube's "confirm you're not a bot"
# Set once from --cookies-from-browser in main(); used by every yt-dlp call.
# ==============================================================================

COOKIES_FROM_BROWSER = None  # e.g. "chrome", "firefox" — set from --cookies-from-browser
COOKIE_FILE          = None  # path to a Netscape cookies.txt — set from --cookies

def _cookie_opts():
    """Shared yt-dlp options injecting cookies, if configured."""
    opts = {}
    if COOKIE_FILE:
        opts["cookiefile"] = COOKIE_FILE
    if COOKIES_FROM_BROWSER:
        opts["cookiesfrombrowser"] = (COOKIES_FROM_BROWSER,)
    return opts


def extract_video_id(url):
    """Pull the 11-char YouTube video id straight from the URL (no network).

    Lets caption-only videos proceed even when yt-dlp metadata is blocked.
    """
    m = re.search(r"(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None


# ==============================================================================
# METADATA — fetch title / channel / id via yt-dlp without downloading
# ==============================================================================

def fetch_metadata(url):
    """Return (title, channel, video_id, duration) for a YouTube URL (no download).

    `duration` is the video length in seconds (int), or None if unavailable.
    """
    import yt_dlp

    ydl_opts = {"quiet": True, "no_warnings": True, "skip_download": True, **_cookie_opts()}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        # process=False skips format selection — avoids YouTube's "n-challenge"
        # (which needs a JS runtime) so we can still read title/channel/duration.
        info = ydl.extract_info(url, download=False, process=False)

    title    = info.get("title", "Untitled")
    channel  = info.get("channel") or info.get("uploader") or "Unknown channel"
    video_id = info.get("id") or extract_video_id(url)
    duration = info.get("duration")  # seconds, or None
    return title, channel, video_id, duration


def format_duration(seconds):
    """Format a duration in seconds as H:MM:SS (or M:SS under an hour)."""
    if not seconds:
        return "unknown"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# ==============================================================================
# TIER 1 — youtube-transcript-api (creator captions)
# ==============================================================================

def fetch_captions(video_id):
    """Return caption text for a video, or None if no captions are available.

    Supports both the classic static API (<1.0) and the instance API (>=1.0)
    of youtube-transcript-api.
    """
    from youtube_transcript_api import YouTubeTranscriptApi

    langs = ["en", "en-US", "en-GB"]
    try:
        try:
            # Classic API (youtube-transcript-api < 1.0)
            segments = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
            return " ".join(seg["text"] for seg in segments)
        except AttributeError:
            # Instance API (youtube-transcript-api >= 1.0)
            fetched = YouTubeTranscriptApi().fetch(video_id, languages=langs)
            return " ".join(snippet.text for snippet in fetched)
    except Exception as e:
        # No transcript, transcripts disabled, language missing, throttled, etc.
        # Surface the reason — distinguishes "no captions" from a 429/IP block.
        print(f"   ℹ️  No captions via API ({type(e).__name__})")
        return None


# ==============================================================================
# TIER 2 — yt-dlp audio download + Whisper large-v3
# ==============================================================================

def download_audio(url):
    """Download audio only into a temp file. Returns the audio file path.

    yt-dlp may write a different extension than expected (the selected format can
    be remuxed/renamed), so we capture the actual file via a progress hook rather
    than guessing the name.
    """
    import yt_dlp

    print(f"⬇️  Fetching audio: {url}")

    finished = []

    def _hook(d):
        if d.get("status") == "finished" and d.get("filename"):
            finished.append(d["filename"])

    temp_dir = os.path.join(ROOT, "_yt_temp")
    os.makedirs(temp_dir, exist_ok=True)

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(temp_dir, "_yt_%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [_hook],
        **_cookie_opts(),
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    video_id = info.get("id", "")

    audio_path = None
    if finished:
        audio_path = finished[-1]
    if not (audio_path and os.path.exists(audio_path)):
        for dl in info.get("requested_downloads") or []:
            if dl.get("filepath") and os.path.exists(dl["filepath"]):
                audio_path = dl["filepath"]
                break
    if not (audio_path and os.path.exists(audio_path)):
        matches = [
            m for m in Path(temp_dir).glob(f"_yt_{video_id}.*")
            if m.suffix not in (".part", ".ytdl")
        ]
        if matches:
            audio_path = str(matches[0])

    if not (audio_path and os.path.exists(audio_path)):
        raise FileNotFoundError(f"Downloaded audio for {url} could not be located in {temp_dir}")

    return audio_path


def transcribe(audio_path, language=WHISPER_LANGUAGE):
    """Transcribe audio with Whisper large-v3 on the GPU (CUDA required).

    GPU-only by design: if no CUDA device is visible we raise rather than fall
    back to CPU — transcribing large-v3 on CPU is unacceptably slow. If this
    fires, the installed PyTorch is the CPU-only build; install a CUDA wheel
    (e.g. `torch==2.12.0+cu126`).

    `language` is forced (not auto-detected) by default: auto-detection misfires
    on music/silent intros and can lock onto the wrong language (e.g. Norwegian),
    then render English speech as that language. Pass "auto" to auto-detect.
    """
    import whisper
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA GPU not available — refusing to transcribe on CPU. "
            "The installed PyTorch has no CUDA support; install a CUDA build, "
            "e.g.: py -3.11 -m pip install torch==2.12.0+cu126 "
            "--index-url https://download.pytorch.org/whl/cu126"
        )

    lang = None if str(language).lower() in ("auto", "none", "") else language

    gpu_name = torch.cuda.get_device_name(0)
    print(f"🎧 Loading Whisper ({WHISPER_MODEL}) on GPU [{gpu_name}] — first run downloads the model, may take a while...")
    model = whisper.load_model(WHISPER_MODEL, device="cuda")
    print(f"📝 Transcribing on GPU (language={lang or 'auto'}) — this is the slow part...")
    result = model.transcribe(
        audio_path,
        language=lang,                     # force language; None = auto-detect
        condition_on_previous_text=False,  # stop the repeat-loop hallucination
        verbose=True,
        fp16=True,                         # GPU half-precision (faster, less VRAM)
    )
    return result["text"].strip()


def whisper_transcript(url, language=WHISPER_LANGUAGE):
    """Download audio, transcribe with Whisper, and delete the temp audio."""
    audio_path = None
    try:
        audio_path = download_audio(url)
        return transcribe(audio_path, language=language)
    finally:
        # Delete the temp audio (and any stray fragments) immediately.
        if audio_path:
            parent = Path(audio_path).parent
            stem = Path(audio_path).stem
            removed = False
            for f in parent.glob(stem + ".*"):
                try:
                    f.unlink()
                    removed = True
                except OSError:
                    pass
            if removed:
                print("🧹 Deleted temp audio file.")


# ==============================================================================
# TRANSCRIPT CLEANING
# ==============================================================================

# Common non-speech annotations YouTube captions / Whisper emit.
_NOISE_TAGS = re.compile(
    r"\[(?:music|applause|laughter|cheering|cheers|crosstalk|inaudible|"
    r"background noise|silence|foreign|sighs|coughs)\]",
    re.IGNORECASE,
)

def clean_transcript(text):
    """Strip [Music]/[Applause]-style tags and normalise whitespace."""
    text = _NOISE_TAGS.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def looks_like_hallucination(text):
    """True if a Whisper transcript is a degenerate hallucination loop.

    On silent / music-only audio Whisper repeats the same short phrase over and
    over ("Thank you for watching."). Catch it so it never reaches the DB. Two
    cheap signals: very low lexical diversity, or one sentence dominating.
    """
    words = text.split()
    if len(words) < 40:
        return False  # too short to judge confidently
    if len(set(w.lower() for w in words)) / len(words) < 0.15:
        return True
    sentences = [s.strip().lower() for s in re.split(r"[.!?]+", text) if s.strip()]
    if len(sentences) >= 5:
        from collections import Counter
        _, top = Counter(sentences).most_common(1)[0]
        if top / len(sentences) > 0.5:
            return True
    return False


# ==============================================================================
# GEMINI EMBEDDING
# ==============================================================================

def embed_texts(client, texts):
    """Embed a list of texts with Gemini, returning a list of vectors."""
    result = client.models.embed_content(
        model=GEMINI_EMBED_MODEL,
        contents=texts,
    )
    return [e.values for e in result.embeddings]


# ==============================================================================
# PER-VIDEO PIPELINE
# ==============================================================================

splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,        # 1400 per config
    chunk_overlap=CHUNK_OVERLAP,  # 200 per config
    separators=["\n\n", "\n", ". ", " "],
)


def process_video(url, agent_name, collection, gemini_client,
                  language=WHISPER_LANGUAGE, allow_whisper=False):
    """Ingest one YouTube video into the agent's philosophy collection.

    Captions-first: try youtube-transcript-api (no yt-dlp) before anything else.
    The Whisper audio-download fallback is opt-in via `allow_whisper` because it
    needs yt-dlp + a JS runtime to beat YouTube's n-challenge.

    Returns True on success (or clean skip), False on failure.
    """
    # --- Duplicate detection (by URL) ------------------------------------------
    if collection.find_one({"url": url}, {"_id": 1}):
        print(f"⏭️  Already ingested — skipping: {url}")
        return True

    # --- Video id (no network — lets captions work even if yt-dlp is blocked) --
    video_id = extract_video_id(url)
    if not video_id:
        print(f"   ❌ Could not determine a video id for {url} — skipping.")
        return False

    # --- Tier 1: captions first (no yt-dlp) ------------------------------------
    raw = fetch_captions(video_id)

    # Decide the source tier before doing heavier work.
    if raw:
        source_tier = "auto-captions"
    elif allow_whisper:
        source_tier = "Whisper large-v3"
    else:
        print(f"   🚫 No captions available for {url} — skipping "
              f"(Whisper download disabled; pass --whisper to enable).")
        return False

    # --- Metadata (best-effort enrichment; never fatal) ------------------------
    try:
        title, channel, _vid, duration = fetch_metadata(url)
    except Exception as e:
        title, channel, duration = (video_id or url), "Unknown channel", None
        print(f"   ⚠️  Metadata lookup failed ({type(e).__name__}) — using limited info.")

    print(f"\n📺 {title}")
    print(f"   Channel: {channel}")
    print(f"   Length : {format_duration(duration)}")

    # --- Transcript ------------------------------------------------------------
    if raw:
        print("📄 Transcript source: auto-captions")
    else:
        print("🎧 Transcript source: Whisper large-v3")
        raw = whisper_transcript(url, language=language)

    transcript = clean_transcript(raw or "")
    if not transcript:
        print("⚠️  Empty transcript — nothing to ingest.")
        return False

    # Whisper (not trusted captions) can still hallucinate on silent/music audio.
    if source_tier == "Whisper large-v3" and looks_like_hallucination(transcript):
        print("⚠️  Whisper produced a degenerate/hallucinated transcript "
              "(repeated filler) — skipping, not ingesting.")
        return False

    # --- Chunk -----------------------------------------------------------------
    chunks = splitter.split_text(transcript)
    chunks = [c for c in chunks if len(c.strip()) > 100]
    if not chunks:
        print("⚠️  No usable chunks after cleaning — skipping.")
        return False

    # --- Embed + store in batches (Gemini embed → MongoDB insert) --------------
    ingested_at = datetime.now(timezone.utc).isoformat()
    n_chunks = len(chunks)
    n_batches = (n_chunks + EMBED_BATCH_SIZE - 1) // EMBED_BATCH_SIZE
    added = 0

    for b in range(n_batches):
        start = b * EMBED_BATCH_SIZE
        end = min(start + EMBED_BATCH_SIZE, n_chunks)
        batch_chunks = chunks[start:end]

        print(f"   Embedding batch {b + 1}/{n_batches}...")
        vectors = embed_texts(gemini_client, batch_chunks)

        docs = []
        for offset, (chunk, vec) in enumerate(zip(batch_chunks, vectors)):
            idx = start + offset
            docs.append({
                "_id":         f"{agent_name}_{video_id}_{idx}",
                "text":        chunk,
                "embedding":   vec,
                "source":      title,
                "source_type": "youtube",
                "url":         url,
                "channel":     channel,
                "agent":       agent_name,
                "chunk":       idx,
                "ingested_at": ingested_at,
            })

        collection.insert_many(docs)
        added += len(docs)

        time.sleep(BATCH_SLEEP_SECS)

    # --- Summary ---------------------------------------------------------------
    total = collection.count_documents({})
    print(f'✅ Ingested: "{title}"')
    print(f"   Transcript : {source_tier}")
    print(f"   Chunks added : {added}")
    print(f"   Collection   : {collection.name} ({total:,} total docs)")
    return True


# ==============================================================================
# BULK MODE
# ==============================================================================

def read_url_list(list_path):
    """Read a urls.txt — one URL per line, '#' starts a comment, blanks ignored."""
    if not os.path.exists(list_path):
        print(f"❌ URL list not found: {list_path}")
        sys.exit(1)
    with open(list_path, "r", encoding="utf-8") as f:
        return [
            line.strip() for line in f
            if line.strip() and not line.strip().startswith("#")
        ]


# ==============================================================================
# MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="Ingest YouTube transcripts into an agent's RAG brain (MongoDB + Gemini)")
    parser.add_argument("--agent", required=True, help="Agent name e.g. buffett, cathie_wood")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Single YouTube URL to ingest")
    group.add_argument("--bulk", help="Path to a urls.txt file (one URL per line)")
    parser.add_argument(
        "--language", default=WHISPER_LANGUAGE,
        help=f"Whisper transcription language (default from config: {WHISPER_LANGUAGE!r}). "
             f"Use 'auto' to auto-detect. Only affects the Whisper fallback, not captions.",
    )
    parser.add_argument(
        "--cookies-from-browser", dest="cookies_from_browser", default=None,
        metavar="BROWSER",
        help="Browser to read YouTube login cookies from (chrome, edge, firefox, brave, ...). "
             "Use this when YouTube returns 'Sign in to confirm you're not a bot'. "
             "On Windows, Chrome/Edge often fail (locked/encrypted DB) — prefer --cookies.",
    )
    parser.add_argument(
        "--cookies", dest="cookies_file", default=None, metavar="FILE",
        help="Path to an exported Netscape cookies.txt file. Most reliable way past "
             "YouTube's bot-check on Windows (avoids the Chrome cookie-DB lock).",
    )
    parser.add_argument(
        "--whisper", action="store_true",
        help="Enable the Whisper audio-download fallback for caption-less videos. "
             "Off by default — needs yt-dlp + a JS runtime (Deno) for YouTube's n-challenge.",
    )
    parser.add_argument(
        "--delay", type=float, default=3.0, metavar="SECONDS",
        help="Seconds to wait between videos in bulk mode (default: 3) to avoid "
             "YouTube rate-limiting (HTTP 429). Use 0 for no delay.",
    )
    args = parser.parse_args()

    # Make cookies available to every yt-dlp call this run.
    global COOKIES_FROM_BROWSER, COOKIE_FILE
    COOKIES_FROM_BROWSER = args.cookies_from_browser
    COOKIE_FILE          = args.cookies_file
    if COOKIE_FILE:
        if not os.path.exists(COOKIE_FILE):
            print(f"❌ Cookies file not found: {COOKIE_FILE}")
            sys.exit(1)
        print(f"🍪 Using cookies file: {COOKIE_FILE}")
    elif COOKIES_FROM_BROWSER:
        print(f"🍪 Using YouTube cookies from browser: {COOKIES_FROM_BROWSER}")

    agent_name = args.agent.lower().replace(" ", "_")
    collection_name = mongo_philosophy_collection(agent_name)

    print("=" * 60)
    print(f"  YouTube Ingestion — {agent_name.replace('_', ' ').title()}")
    print(f"  Collection: {collection_name}")
    print("=" * 60)

    # --- Gemini client ---
    if not GOOGLE_API_KEY:
        print("\n  ERROR: GOOGLE_API_KEY is not set (.env). Cannot embed.")
        sys.exit(1)
    print(f"\n  Embedding model: {GEMINI_EMBED_MODEL}")
    gemini_client = genai.Client(api_key=GOOGLE_API_KEY)

    # --- MongoDB Atlas collection ---
    if not MONGODB_URI:
        print("\n  ERROR: MONGODB_URI is not set (.env). Cannot connect.")
        sys.exit(1)
    mongo_client = MongoClient(MONGODB_URI, server_api=ServerApi('1'))
    db = mongo_client[MONGODB_DB_NAME]
    collection = db[collection_name]

    if args.whisper:
        print("  Whisper fallback: ENABLED (caption-less videos will be downloaded)")
    else:
        print("  Whisper fallback: disabled (captions-only; caption-less videos skipped)")

    if args.url:
        ok = process_video(args.url, agent_name, collection, gemini_client,
                            language=args.language, allow_whisper=args.whisper)
        mongo_client.close()
        sys.exit(0 if ok else 1)

    # Bulk mode
    urls = read_url_list(args.bulk)
    if not urls:
        print(f"⚠️  No URLs found in {args.bulk}")
        mongo_client.close()
        return

    total = len(urls)
    print(f"📦 Bulk mode: {total} video(s) queued from {args.bulk}")

    succeeded, failed = 0, 0
    for i, url in enumerate(urls, 1):
        if i > 1 and args.delay > 0:
            time.sleep(args.delay)  # be gentle — avoid YouTube's 429 rate-limit
        print(f"\n{'=' * 60}")
        print(f"▶️  Video {i} of {total}: {url}")
        print(f"{'=' * 60}")
        try:
            ok = process_video(url, agent_name, collection, gemini_client,
                               language=args.language, allow_whisper=args.whisper)
        except Exception as e:
            # Skip failures without aborting the batch.
            print(f"❌ Error processing {url}: {e}")
            ok = False
        succeeded += 1 if ok else 0
        failed += 0 if ok else 1

    print(f"\n{'=' * 60}")
    print(f"🏁 Bulk complete: {succeeded} succeeded, {failed} failed (of {total}).")
    print(f"{'=' * 60}")

    mongo_client.close()


if __name__ == "__main__":
    main()
