# Voice Capture

Two-tap voice memo capture for iPhone → server-side transcription via mlx-whisper → Claude Chat session.

## Overview

A lightweight service that:
1. Accepts audio uploads from an iOS Shortcut (two taps: record → stop/send)
2. Transcribes using mlx-whisper locally on Apple Silicon (with prompt conditioning for domain vocabulary)
3. Creates a Claude Chat session with the transcribed text

## Architecture

```
iPhone Shortcut          Voice Capture Server         Claude Chat
┌─────────────┐         ┌──────────────────────┐    ┌─────────────┐
│ Tap: Record  │         │ POST /api/voice      │    │ Webhook API │
│ Tap: Stop    │────────▶│  └─ Save audio       │───▶│  └─ Session │
│ Upload .m4a  │         │  └─ mlx-whisper      │    │  └─ Claude  │
└─────────────┘         │  └─ Prompt condition  │    └─────────────┘
                        └──────────────────────┘
```

## Requirements

- Python 3.11+
- Apple Silicon Mac (for mlx-whisper)
- Claude Chat instance running (for session creation)

## Setup

```bash
pip install mlx-whisper flask
```
