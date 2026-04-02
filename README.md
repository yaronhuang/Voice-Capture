# Voice Capture

Voice memo → automatic transcription → Claude post-processing → action.

## Overview

Record a voice memo on iPhone or Apple Watch. It syncs to your Mac via iCloud, gets transcribed by two models (Parakeet + Whisper), and Claude processes the transcript via email — logging medications, journaling, or answering questions.

## Architecture

```
iPhone/Watch                Mac (automatic)                    Claude
┌─────────────┐            ┌──────────────────────────┐       ┌──────────┐
│ Voice Memos │  iCloud    │ launchd detects new file  │ email │ Email    │
│ Record/Stop │───sync────▶│  └─ Normalize audio       │──────▶│ watcher  │
└─────────────┘            │  └─ Parakeet (fast, 1.7s) │       │  └─ Post │
                           │  └─ Whisper + vocab prompt│       │    process│
                           │  └─ Email both transcripts│       │  └─ Act  │
                           └──────────────────────────┘       └──────────┘
```

No custom app, no shortcut, no server endpoint. Just Voice Memos + a launchd folder watcher.

## Why Two Models?

Aaron's speech articulation is affected by radiation therapy. Testing across 6 model sizes and 3 model families showed:

- **Parakeet TDT 0.6B**: Fastest (1.7s/60s audio), never hallucinates into repetition loops. Best for conversational audio.
- **Whisper large-v3-turbo**: Supports prompt conditioning with medical vocabulary. Got "Ritalin 10mg, duloxetine" from audio all other models failed on.

Claude cross-references both transcripts and uses semantic reasoning to fix errors (e.g., "Tyler five hundred celebrates a hundred" → "Tylenol 500, Celebrex 100").

## Setup

### Dependencies

```bash
# Use existing whisper venv
source ~/.venvs/whisper/bin/activate
pip install parakeet-mlx mlx-whisper requests
```

### Install launchd service

```bash
cp com.aronhuang.voice-capture.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.aronhuang.voice-capture.plist
```

Survives restarts. Watches `~/Library/Group Containers/group.com.apple.VoiceMemos.shared/Recordings/` for new files.

### Verify

```bash
launchctl list | grep voice-capture
```

## Files

| File | Purpose |
|------|---------|
| `watcher.py` | Main pipeline script triggered by launchd |
| `vocab_prompt.txt` | Medical vocabulary for Whisper prompt conditioning |
| `CLAUDE.md` | Context for Claude sessions processing voice memos |
| `com.aronhuang.voice-capture.plist` | launchd service definition |

## Usage

1. Record a Voice Memo on iPhone or Apple Watch
2. Wait for iCloud sync (~5-15 seconds)
3. Pipeline runs automatically
4. Receive email with Claude's interpretation and action taken
