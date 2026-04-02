#!/usr/bin/env python3
"""Voice Capture — watch for new Voice Memos and transcribe them.

Triggered by launchd WatchPaths when the Voice Memos directory changes.
Processes new .m4a files through Parakeet + Whisper, then sends both
transcripts to Claude Chat's webhook for semantic post-processing.
"""

import hashlib
import json
import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VOICE_MEMOS_DIR = Path.home() / "Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings"
STATE_FILE = Path(__file__).parent / "state.json"
VOCAB_PROMPT_FILE = Path(__file__).parent / "vocab_prompt.txt"
LOG_FILE = Path(__file__).parent / "voice-capture.log"

GMAIL_VENV = Path.home() / ".venvs/gmail"
GMAIL_SCRIPT = Path.home() / ".claude/skills/gmail/scripts/gmail_tool.py"

# How old a file can be and still get processed (seconds).
# Prevents processing the entire backlog on first run.
MAX_AGE_SECONDS = 300  # 5 minutes

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("voice-capture")

# ---------------------------------------------------------------------------
# State management — track which files we've already processed
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"processed": []}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2))


def file_hash(path: Path) -> str:
    """Quick hash: filename + size + mtime."""
    stat = path.stat()
    return hashlib.md5(f"{path.name}:{stat.st_size}:{stat.st_mtime}".encode()).hexdigest()

# ---------------------------------------------------------------------------
# Audio preprocessing
# ---------------------------------------------------------------------------

def normalize_audio(src: Path, dst: Path):
    """Convert to 16 kHz mono WAV with loudness normalization."""
    subprocess.run(
        [
            "ffmpeg", "-i", str(src),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-ar", "16000", "-ac", "1",
            "-f", "wav", str(dst), "-y",
        ],
        capture_output=True,
        check=True,
    )

# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe_parakeet(wav_path: Path) -> str:
    from parakeet_mlx import from_pretrained
    model = from_pretrained("mlx-community/parakeet-tdt-0.6b-v3")
    result = model.transcribe(str(wav_path))
    return result.text if hasattr(result, "text") else str(result)


def transcribe_whisper(wav_path: Path, vocab_prompt: str | None = None) -> str:
    import mlx_whisper
    kwargs = dict(
        path_or_hf_repo="mlx-community/whisper-large-v3-turbo",
        language="en",
        condition_on_previous_text=False,
        temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
        compression_ratio_threshold=1.5,
        no_speech_threshold=0.4,
    )
    if vocab_prompt:
        kwargs["initial_prompt"] = vocab_prompt
    result = mlx_whisper.transcribe(str(wav_path), **kwargs)
    text = result["text"].strip()
    # Strip hallucination loops: if any word repeats >5 times in a row, truncate
    words = text.split()
    cleaned = []
    repeat_count = 0
    for i, w in enumerate(words):
        if i > 0 and w == words[i - 1]:
            repeat_count += 1
            if repeat_count >= 5:
                break
        else:
            repeat_count = 0
        cleaned.append(w)
    return " ".join(cleaned)


def load_vocab_prompt() -> str | None:
    if VOCAB_PROMPT_FILE.exists():
        return VOCAB_PROMPT_FILE.read_text().strip()
    return None

# ---------------------------------------------------------------------------
# Claude Chat webhook
# ---------------------------------------------------------------------------

def send_to_claude(parakeet_text: str, whisper_text: str, filename: str, duration: float):
    """Send transcripts as an email to Aaron via Gmail skill."""
    subject = f"Voice memo ({duration:.0f}s)"
    body = (
        "Aaron recorded a voice memo. Read Voice-Capture/CLAUDE.md for full context "
        "on post-processing his speech.\n\n"
        f"Transcript A (Parakeet): \"{parakeet_text}\"\n"
        f"Transcript B (Whisper w/ medical vocab): \"{whisper_text}\"\n\n"
        "Cross-reference both transcripts, infer what Aaron said, take action, "
        "and reply with what you understood."
    )

    try:
        # Send to Claude's inbox (✅ queue) for processing
        result = subprocess.run(
            [
                GMAIL_VENV / "bin" / "python",
                str(GMAIL_SCRIPT),
                "send", subject, body,
                "--to", "self",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            log.info("Email to Claude: %s", result.stdout.strip()[:100])
        else:
            log.error("Email to Claude failed: %s", result.stderr.strip()[:200])
            return False

        # Forward a copy to Aaron so he can see what Claude received
        fwd_body = f"[Voice Capture] Forwarding what Claude received:\n\n---\n\n{body}"
        subprocess.run(
            [
                GMAIL_VENV / "bin" / "python",
                str(GMAIL_SCRIPT),
                "send", f"Fwd: {subject}", fwd_body,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return True
    except Exception as e:
        log.error("Email failed: %s", e)
        return False

# ---------------------------------------------------------------------------
# Get audio duration
# ---------------------------------------------------------------------------

def get_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except Exception:
        return 0.0

# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def process_file(m4a: Path):
    log.info("Processing: %s", m4a.name)
    duration = get_duration(m4a)
    log.info("  Duration: %.1fs", duration)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)

    try:
        # 1. Normalize
        normalize_audio(m4a, wav_path)

        # 2. Transcribe with both models
        vocab = load_vocab_prompt()

        log.info("  Running Parakeet...")
        t0 = time.time()
        parakeet_text = transcribe_parakeet(wav_path)
        log.info("  Parakeet (%.1fs): %s", time.time() - t0, parakeet_text[:100])

        log.info("  Running Whisper...")
        t0 = time.time()
        whisper_text = transcribe_whisper(wav_path, vocab)
        log.info("  Whisper (%.1fs): %s", time.time() - t0, whisper_text[:100])

        # 3. Send to Claude via email
        send_to_claude(parakeet_text, whisper_text, m4a.name, duration)

    finally:
        wav_path.unlink(missing_ok=True)


def main():
    log.info("Voice Capture watcher triggered")

    # Wait briefly for iCloud sync to finish writing the .m4a file.
    # launchd triggers on the DB update, but the audio file may arrive
    # a few seconds later.
    time.sleep(5)

    if not VOICE_MEMOS_DIR.exists():
        log.error("Voice Memos directory not found: %s", VOICE_MEMOS_DIR)
        sys.exit(1)

    state = load_state()
    processed = set(state.get("processed", []))
    now = time.time()
    new_count = 0

    for m4a in sorted(VOICE_MEMOS_DIR.glob("*.m4a")):
        fh = file_hash(m4a)
        if fh in processed:
            continue

        # Skip files older than MAX_AGE_SECONDS
        age = now - m4a.stat().st_mtime
        if age > MAX_AGE_SECONDS:
            # Mark as processed so we don't check again
            processed.add(fh)
            continue

        try:
            process_file(m4a)
            new_count += 1
        except Exception:
            log.exception("Failed to process %s", m4a.name)

        processed.add(fh)

    state["processed"] = list(processed)
    save_state(state)
    log.info("Done. Processed %d new file(s).", new_count)


if __name__ == "__main__":
    main()
