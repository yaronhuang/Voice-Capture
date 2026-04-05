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


def transcribe_apple(m4a_path: Path) -> str:
    """Transcribe using macOS on-device speech recognition (SFSpeechRecognizer)."""
    import Speech
    from Foundation import NSURL, NSRunLoop, NSDate

    recognizer = Speech.SFSpeechRecognizer.alloc().init()
    if not recognizer.isAvailable():
        return ""

    audio_url = NSURL.fileURLWithPath_(str(m4a_path))
    request = Speech.SFSpeechURLRecognitionRequest.alloc().initWithURL_(audio_url)
    request.setShouldReportPartialResults_(False)
    request.setRequiresOnDeviceRecognition_(True)

    final_text = [None]
    error_msg = [None]

    def handler(result, error, ft=final_text, em=error_msg):
        if error:
            em[0] = str(error.localizedDescription())
        if result and result.isFinal():
            ft[0] = result.bestTranscription().formattedString()

    recognizer.recognitionTaskWithRequest_resultHandler_(request, handler)

    for _ in range(30):
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(1.0))
        if final_text[0] or error_msg[0]:
            break

    if error_msg[0]:
        log.warning("Apple dictation error: %s", error_msg[0])
    return final_text[0] or ""


def load_vocab_prompt() -> str | None:
    if VOCAB_PROMPT_FILE.exists():
        return VOCAB_PROMPT_FILE.read_text().strip()
    return None

# ---------------------------------------------------------------------------
# Claude Chat webhook
# ---------------------------------------------------------------------------

def _parse_recording_time(filename: str) -> tuple[str, str]:
    """Parse timestamp from recording filename.

    Supports two formats:
      Voice Memos: '20260402 064548-C926ECA0.m4a'
      HTTP upload: '20260405-104251-8c224fbc.m4a'

    Returns (date_str "04/02/26", time_str "6:45 AM") or ("", "").
    """
    from datetime import datetime
    # Voice Memos format: "YYYYMMDD HHMMSS-..."
    try:
        parts = filename.split("-")[0].strip()
        dt = datetime.strptime(parts, "%Y%m%d %H%M%S")
        return dt.strftime("%m/%d/%y"), dt.strftime("%-I:%M %p")
    except (ValueError, IndexError):
        pass
    # HTTP upload format: "YYYYMMDD-HHMMSS-..."
    try:
        segments = filename.split("-")
        dt = datetime.strptime(f"{segments[0]} {segments[1]}", "%Y%m%d %H%M%S")
        return dt.strftime("%m/%d/%y"), dt.strftime("%-I:%M %p")
    except (ValueError, IndexError):
        return "", ""


def send_to_claude(apple_text: str, parakeet_text: str, whisper_text: str, filename: str, duration: float):
    """Send transcripts as an email to Aaron via Gmail skill."""
    date_str, time_str = _parse_recording_time(filename)

    # Subject: date + parakeet transcript preview (one thread per recording)
    preview = parakeet_text[:50] + ("..." if len(parakeet_text) > 50 else "")
    time_part = f" {time_str}" if time_str else ""
    subject = f"[Voice {date_str}{time_part}] {preview}"

    body = (
        f"Voice memo ({duration:.0f}s). "
        "Read Voice-Capture/CLAUDE.md for full context.\n\n"
        f"Transcript A (Apple Dictation): \"{apple_text}\"\n"
        f"Transcript B (Parakeet): \"{parakeet_text}\"\n"
        f"Transcript C (Whisper w/ medical vocab): \"{whisper_text}\"\n\n"
        "Cross-reference all three transcripts, infer what Aaron said, take action, "
        "and reply with what you understood."
    )

    try:
        # Send to Claude's inbox (✅ queue) for processing
        result = subprocess.run(
            [GMAIL_VENV / "bin" / "python", str(GMAIL_SCRIPT),
             "send", subject, body, "--to", "self"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            log.error("Email to Claude failed: %s", result.stderr.strip()[:200])
            return False

        log.info("Email to Claude: %s", result.stdout.strip()[:100])

        # Get thread ID from the sent email for the forward copy
        thread_id = ""
        try:
            resp = json.loads(result.stdout.strip())
            thread_id = resp.get("threadId", "")
        except (json.JSONDecodeError, ValueError):
            pass

        # Forward a copy to Aaron in the same thread
        fwd_body = f"[Voice Capture] Forwarding what Claude received:\n\n---\n\n{body}"
        fwd_cmd = [
            GMAIL_VENV / "bin" / "python", str(GMAIL_SCRIPT),
            "send", subject, fwd_body,
        ]
        if thread_id:
            fwd_cmd.extend(["--thread-id", thread_id])
        subprocess.run(fwd_cmd, capture_output=True, text=True, timeout=30)
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

        # 2. Transcribe with all three models
        vocab = load_vocab_prompt()

        log.info("  Running Apple Dictation...")
        t0 = time.time()
        apple_text = transcribe_apple(m4a)  # uses original m4a, not normalized wav
        log.info("  Apple (%.1fs): %s", time.time() - t0, apple_text[:100])

        log.info("  Running Parakeet...")
        t0 = time.time()
        parakeet_text = transcribe_parakeet(wav_path)
        log.info("  Parakeet (%.1fs): %s", time.time() - t0, parakeet_text[:100])

        log.info("  Running Whisper...")
        t0 = time.time()
        whisper_text = transcribe_whisper(wav_path, vocab)
        log.info("  Whisper (%.1fs): %s", time.time() - t0, whisper_text[:100])

        # 3. Send to Claude via email
        send_to_claude(apple_text, parakeet_text, whisper_text, m4a.name, duration)

    finally:
        wav_path.unlink(missing_ok=True)


def main():
    # Direct file mode: invoked by server.py for HTTP-uploaded audio
    if len(sys.argv) > 1 and sys.argv[1] == "--file":
        target = Path(sys.argv[2])
        if not target.exists():
            log.error("File not found: %s", target)
            sys.exit(1)
        log.info("Direct file mode: %s", target.name)
        process_file(target)
        return

    log.info("Voice Capture watcher triggered")

    if not VOICE_MEMOS_DIR.exists():
        log.error("Voice Memos directory not found: %s", VOICE_MEMOS_DIR)
        sys.exit(1)

    state = load_state()
    processed = set(state.get("processed", []))
    new_count = 0

    # Re-glob after each pass to catch files that arrived during processing.
    while True:
        found_new = False
        for m4a in sorted(VOICE_MEMOS_DIR.glob("*.m4a")):
            fh = file_hash(m4a)
            if fh in processed:
                continue

            found_new = True
            try:
                process_file(m4a)
                new_count += 1
            except Exception:
                log.exception("Failed to process %s", m4a.name)

            processed.add(fh)

        if not found_new:
            break

    state["processed"] = list(processed)
    save_state(state)
    log.info("Done. Processed %d new file(s).", new_count)


if __name__ == "__main__":
    main()
