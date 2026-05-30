import os
import sys
import re
import warnings
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import yt_dlp
import whisper
import anthropic

# Harden stdout so emoji / UTF-8 prints never raise under a non-UTF-8 console
# (same reason as watcher.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Whisper warns "FP16 is not supported on CPU; using FP32 instead" on every CPU
# transcription. It's harmless noise on a CPU box — silence it.
warnings.filterwarnings("ignore", message="FP16 is not supported on CPU")

load_dotenv(r"C:\Users\peckh\Desktop\OneDrive\VSC Projects\The-Blob\.env")

# Config
PROJECT_DIR = r"C:\Users\peckh\Desktop\OneDrive\VSC Projects\The-Blob"
OUTPUT_DIR = r"C:\Users\peckh\Desktop\OneDrive\The Blob\The Blob\raw-sources\youtube"
WHISPER_MODEL = "large-v3"
CLAUDE_MODEL = "claude-sonnet-4-6"

anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def sanitize_filename(title):
    """Strip characters that break Windows filenames."""
    name = re.sub(r'[<>:"/\\|?*]', "", title)   # illegal on Windows
    name = re.sub(r"[\x00-\x1f]", "", name)      # control chars
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name[:150] or "youtube_video"


def download_audio(url):
    """Fetch the title and download audio only into a temp file in the project folder.

    Returns (title, audio_path).
    """
    print(f"⬇️  Fetching audio: {url}")

    # yt-dlp may write a different extension than info["ext"] implies (the
    # selected format can be remuxed/renamed), so prepare_filename() is not a
    # reliable path. Capture the actual file yt-dlp finishes writing via a
    # progress hook instead of assuming the name.
    finished = []

    def _hook(d):
        if d.get("status") == "finished" and d.get("filename"):
            finished.append(d["filename"])

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(PROJECT_DIR, "_yt_temp_%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [_hook],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    title = info.get("title", "Untitled")
    video_id = info.get("id", "")

    # Resolve the real audio path, most-reliable source first:
    audio_path = None
    # 1) Path the progress hook reported on the "finished" event.
    if finished:
        audio_path = finished[-1]
    # 2) Post-download metadata yt-dlp records on the info dict.
    if not (audio_path and os.path.exists(audio_path)):
        for dl in info.get("requested_downloads") or []:
            if dl.get("filepath") and os.path.exists(dl["filepath"]):
                audio_path = dl["filepath"]
                break
    # 3) Last resort: list the temp dir for the file carrying this video id,
    #    ignoring any leftover partial-download fragments.
    if not (audio_path and os.path.exists(audio_path)):
        matches = [
            m for m in Path(PROJECT_DIR).glob(f"_yt_temp_{video_id}.*")
            if m.suffix not in (".part", ".ytdl")
        ]
        if matches:
            audio_path = str(matches[0])

    if not (audio_path and os.path.exists(audio_path)):
        raise FileNotFoundError(f"Downloaded audio for {url} could not be located in {PROJECT_DIR}")

    return title, audio_path


def transcribe(audio_path):
    """Transcribe audio with Whisper large-v3."""
    # --- DEBUG ---
    print("🐞 [DEBUG] transcribe() received:")
    print(f"🐞   audio_path:      {audio_path!r}")
    print(f"🐞   os.path.abspath: {os.path.abspath(audio_path)!r}")
    print(f"🐞   exists? {os.path.exists(audio_path)}")
    # --- END DEBUG ---
    print(f"🎧 Loading Whisper ({WHISPER_MODEL}) — first run downloads the model, may take a while...")
    model = whisper.load_model(WHISPER_MODEL)
    print("📝 Transcribing (this is the slow part)...")
    result = model.transcribe(audio_path, verbose=True)
    return result["text"].strip()


def structure_notes(title, transcript):
    """Send the raw transcript to Claude and get back structured Markdown notes."""
    print(f"🧠 Structuring notes with {CLAUDE_MODEL}...")
    response = anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8000,
        messages=[{
            
            "role": "user",
            "content": f"""You are turning a raw, auto-transcribed YouTube video into clean, durable study notes for a personal knowledge base (a "second brain"). The transcript has unreliable punctuation, no speaker labels, and may contain filler.

Produce well-structured Markdown notes that capture the core teachable content — the ideas, explanations, frameworks, examples, numbers, and actionable takeaways someone would want months later WITHOUT rewatching the video.

Rules:
- Use dynamic `##` section headers that reflect THIS video's actual topics and natural flow. Do NOT use a fixed template — let the content dictate the structure.
- Be faithful to the transcript; do not invent facts. If something is unclear, omit it rather than guessing.
- Prioritise substance: drop filler, repetition, and sponsor / self-promo segments.
- Keep concrete details worth retaining: definitions, step-by-step processes, specific tools, tickers, numbers, and notable quotes.
- Use bullet points and short paragraphs for scannability. **Bold** key terms.
- Start with a one or two sentence summary of what the video covers, then the sections.
- Output ONLY the Markdown note body. Do NOT include YAML frontmatter, a top-level `#` title, or any preamble like "Here are the notes".

Video title: {title}

Transcript:
{transcript}"""
        }]
    )
    return response.content[0].text.strip()


def save_note(title, url, body):
    """Write the finished note as a .md file into the vault's youtube folder."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, sanitize_filename(title) + ".md")
    date = datetime.now().strftime("%Y-%m-%d")
    note = (
        "---\n"
        f"title: {title}\n"
        f"source_url: {url}\n"
        f"date: {date}\n"
        "type: youtube\n"
        "---\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(note)
    return filepath


def process_youtube(url):
    audio_path = None
    try:
        print(f"\n📺 Processing YouTube video: {url}")

        title, audio_path = download_audio(url)
        print(f"✅ Downloaded: {title}")

        transcript = transcribe(audio_path)
        if not transcript:
            print("⚠️ Empty transcript — nothing to save.")
            return False
        print(f"✅ Transcribed [{len(transcript)} chars]")

        body = structure_notes(title, transcript)
        filepath = save_note(title, url, body)
        print(f"✅ Saved note: {filepath}")
        print("👁️ The watcher will embed it into MongoDB automatically once it lands in the vault.")
        return True

    except Exception as e:
        print(f"❌ Error processing {url}: {e}")
        return False
    finally:
        # Delete the temp audio (and any stray .part files from this run).
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


def process_bulk(list_path):
    """Process every YouTube URL listed (one per line) in `list_path`.

    Blank lines and lines starting with '#' are ignored. Videos are processed
    sequentially; a failing video is logged and skipped rather than aborting the
    whole batch.
    """
    if not os.path.exists(list_path):
        print(f"❌ URL list not found: {list_path}")
        sys.exit(1)

    with open(list_path, "r", encoding="utf-8") as f:
        urls = [
            line.strip() for line in f
            if line.strip() and not line.strip().startswith("#")
        ]

    if not urls:
        print(f"⚠️ No URLs found in {list_path}")
        return

    total = len(urls)
    print(f"📦 Bulk mode: {total} video(s) queued from {list_path}")

    succeeded, failed = 0, 0
    for i, url in enumerate(urls, 1):
        print(f"\n{'=' * 60}")
        print(f"▶️  Video {i} of {total}: {url}")
        print(f"{'=' * 60}")
        try:
            ok = process_youtube(url)
        except Exception as e:
            # process_youtube handles its own errors, but guard the batch anyway
            # so an unexpected escape can't stop the remaining videos.
            print(f"❌ Unexpected error on {url}: {e}")
            ok = False
        if ok:
            succeeded += 1
        else:
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"🏁 Bulk complete: {succeeded} succeeded, {failed} failed (of {total}).")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python youtube_processor.py "<youtube_url>"')
        print("   or: python youtube_processor.py --bulk <urls.txt>")
        sys.exit(1)
    if sys.argv[1] == "--bulk":
        if len(sys.argv) < 3:
            print("Usage: python youtube_processor.py --bulk <urls.txt>")
            sys.exit(1)
        process_bulk(sys.argv[2])
    else:
        process_youtube(sys.argv[1])
