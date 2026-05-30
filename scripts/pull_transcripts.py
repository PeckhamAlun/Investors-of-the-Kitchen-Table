"""
==============================================================================
  PULL_TRANSCRIPTS.PY — YOUTUBE → WHISPER → PHILOSOPHY
  Reads urls.txt from the agent's folder, downloads audio for each unprocessed
  URL, transcribes with Whisper, saves transcript to philosophy folder, and
  tracks processed URLs in processed_urls.txt.
==============================================================================

  USAGE:
      py -3.11 scripts/pull_transcripts.py --agent buffett
      py -3.11 scripts/pull_transcripts.py --agent cathie_wood

  INSTALL:
      py -3.11 -m pip install yt-dlp openai-whisper torch

  NOTES:
      - Requires ffmpeg on PATH. Install from https://ffmpeg.org/download.html
      - Runs Whisper 'medium' model on GPU (CUDA) if available, else CPU
      - Audio deleted automatically after transcription
      - Processed URLs tracked in agents/{agent}/processed_urls.txt
      - Add new URLs to agents/{agent}/urls.txt and re-run — already processed
        URLs are skipped automatically

  FOLDER STRUCTURE:
      agents/{agent}/
          urls.txt              ← you add YouTube URLs here, one per line
          processed_urls.txt    ← auto-managed, do not edit manually
          philosophy/           ← transcripts saved here as .txt files

==============================================================================
"""

import os
import sys
import argparse
import re
from pathlib import Path
from datetime import datetime

# ==============================================================================
# CONFIG
# ==============================================================================

WHISPER_MODEL = "medium"
AUDIO_FORMAT  = "mp3"
SCRIPTS_DIR   = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT  = os.path.dirname(SCRIPTS_DIR)

# ==============================================================================
# HELPERS
# ==============================================================================

def sanitise_filename(title: str) -> str:
    title = re.sub(r'[\\/*?:"<>|]', "", title)
    title = title.replace("\n", " ").strip()
    return title[:120]


def agent_dir(agent: str) -> Path:
    return Path(PROJECT_ROOT) / "agents" / agent


def agent_philosophy_dir(agent: str) -> Path:
    return agent_dir(agent) / "philosophy"


def urls_file(agent: str) -> Path:
    return agent_dir(agent) / "urls.txt"


def processed_file(agent: str) -> Path:
    return agent_dir(agent) / "processed_urls.txt"


def load_urls(agent: str) -> list[str]:
    """Load all URLs from urls.txt, skip blank lines and comments."""
    path = urls_file(agent)
    if not path.exists():
        print(f"\n  ERROR: No urls.txt found at {path}")
        print(f"  Create the file and add YouTube URLs, one per line.")
        sys.exit(1)

    urls = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def load_processed(agent: str) -> set[str]:
    """Load already-processed URLs from processed_urls.txt."""
    path = processed_file(agent)
    if not path.exists():
        return set()
    with open(path, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def mark_processed(agent: str, url: str) -> None:
    """Append a URL to processed_urls.txt."""
    with open(processed_file(agent), "a", encoding="utf-8") as f:
        f.write(url + "\n")


def download_audio(url: str, out_dir: Path) -> tuple[Path, str]:
    """Download audio from YouTube. Returns (audio_path, safe_title)."""
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        info  = ydl.extract_info(url, download=False)
        title = info.get("title", "untitled")

    safe_title = sanitise_filename(title)
    audio_path = out_dir / f"{safe_title}.{AUDIO_FORMAT}"

    ydl_opts = {
        "format":         "bestaudio/best",
        "outtmpl":        str(out_dir / f"{safe_title}.%(ext)s"),
        "postprocessors": [{
            "key":            "FFmpegExtractAudio",
            "preferredcodec": AUDIO_FORMAT,
        }],
        "quiet":          False,
        "no_warnings":    False,
    }

    print(f"\n  Downloading: {title}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    return audio_path, safe_title, title


def transcribe_audio(audio_path: Path) -> str:
    """Transcribe audio using Whisper on GPU if available."""
    import whisper
    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"  Loading Whisper '{WHISPER_MODEL}' on {device.upper()}...")

    model  = whisper.load_model(WHISPER_MODEL, device=device)

    print(f"  Transcribing: {audio_path.name}")
    print(f"  This may take a few minutes...\n")

    result = model.transcribe(
        str(audio_path),
        verbose=False,
        fp16=(device == "cuda"),
    )
    return result["text"].strip()


def save_transcript(text: str, agent: str, safe_title: str, original_title: str) -> Path:
    """Save transcript to agents/{agent}/philosophy/."""
    phil_dir = agent_philosophy_dir(agent)
    phil_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    filename  = f"{timestamp}_{safe_title}.txt"
    out_path  = phil_dir / filename

    header = f"""=== TRANSCRIPT ===
Source  : YouTube
Title   : {original_title}
Agent   : {agent}
Date    : {datetime.now().strftime('%Y-%m-%d %H:%M')}
==================

"""
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + text)

    return out_path


# ==============================================================================
# MAIN
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Batch YouTube → Whisper → agent philosophy folder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  py -3.11 scripts/pull_transcripts.py --agent buffett
  py -3.11 scripts/pull_transcripts.py --agent cathie_wood
  py -3.11 scripts/pull_transcripts.py --agent peter_lynch
        """
    )
    parser.add_argument("--agent", required=True, type=str,
                        help="Agent name (e.g. buffett, cathie_wood)")
    args  = parser.parse_args()
    agent = args.agent.lower().replace(" ", "_")

    # Validate agent folder
    if not agent_dir(agent).exists():
        print(f"\n  ERROR: No agent folder found at agents/{agent}/")
        print(f"  Create the folder first.")
        sys.exit(1)

    all_urls       = load_urls(agent)
    processed_urls = load_processed(agent)
    pending        = [u for u in all_urls if u not in processed_urls]

    print(f"\n  {'='*56}")
    print(f"  PULL TRANSCRIPTS")
    print(f"  {'='*56}")
    print(f"  Agent     : {agent}")
    print(f"  Total URLs: {len(all_urls)}")
    print(f"  Processed : {len(processed_urls)}")
    print(f"  Pending   : {len(pending)}")
    print(f"  Model     : Whisper {WHISPER_MODEL}")
    print(f"  {'='*56}")

    if not pending:
        print(f"\n  All URLs already processed. Nothing to do.")
        print(f"  Add new URLs to agents/{agent}/urls.txt and re-run.\n")
        sys.exit(0)

    # Load Whisper once for all videos
    import whisper
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n  Loading Whisper '{WHISPER_MODEL}' on {device.upper()}...")
    model = whisper.load_model(WHISPER_MODEL, device=device)

    phil_dir = agent_philosophy_dir(agent)
    phil_dir.mkdir(parents=True, exist_ok=True)

    success = 0
    failed  = []

    for i, url in enumerate(pending, 1):
        print(f"\n  {'─'*56}")
        print(f"  [{i}/{len(pending)}] {url}")
        print(f"  {'─'*56}")

        audio_path = None
        try:
            # Download
            audio_path, safe_title, original_title = download_audio(url, phil_dir)

            # Transcribe (reuse loaded model)
            print(f"  Transcribing: {audio_path.name}")
            print(f"  This may take a few minutes...\n")
            result     = model.transcribe(
                str(audio_path),
                verbose=False,
                fp16=(device == "cuda"),
            )
            transcript = result["text"].strip()

            # Save
            out_path = save_transcript(transcript, agent, safe_title, original_title)
            mark_processed(agent, url)
            success += 1

            print(f"\n  Saved : {out_path.relative_to(Path(PROJECT_ROOT))}")
            print(f"  Marked as processed.")

        except Exception as e:
            print(f"\n  ERROR processing {url}: {e}")
            failed.append(url)

        finally:
            if audio_path and audio_path.exists():
                audio_path.unlink()
                print(f"  Audio deleted.")

    # Summary
    print(f"\n  {'='*56}")
    print(f"  COMPLETE")
    print(f"  {'='*56}")
    print(f"  Succeeded : {success}")
    print(f"  Failed    : {len(failed)}")
    if failed:
        print(f"\n  Failed URLs:")
        for u in failed:
            print(f"    {u}")
    print(f"\n  Next step — re-ingest the philosophy collection:")
    print(f"  py -3.11 scripts/ingest_philosophy.py --agent {agent}\n")


if __name__ == "__main__":
    main()