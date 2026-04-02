# Voice Capture

Voice memo transcription pipeline for Aaron. Records from iPhone/Apple Watch sync via iCloud Voice Memos, get transcribed by Parakeet + Whisper on the Mac, and dispatched to Claude via email for post-processing and action.

## Aaron's Speech

Aaron's speech articulation is affected by radiation therapy (cancer treatment). Transcription models often produce wrong words that are phonetically close to the intended words. Examples:

| Transcribed | Actual | Context |
|---|---|---|
| "Tyler five hundred" | "Tylenol 500" | Medication logging |
| "celebrates a hundred" | "Celebrex 100" | Medication logging |
| "tensor keep drawing" | "cancer keeps growing" | Medical discussion |
| "Send her" | "Synthroid" | Medication logging |

## Post-Processing Voice Memos

When you receive a voice memo email with two transcripts (Parakeet + Whisper):

1. **Cross-reference both transcripts** — different models catch different words
2. **Use the vocabulary list** in `vocab_prompt.txt` for medication/medical term hints
3. **Infer meaning from context** — if it sounds like medication names + dosages, it's a medication log
4. **Take action** based on what Aaron said:
   - Question → research and reply
   - Journal entry → save to the appropriate project
   - Task or reminder → create it in the relevant system
   - **Health tracking data** → submit via the `/health-tracking` skill
   - Unclear → ask Aaron to confirm

   Health tracking data includes: medications, supplements, symptoms, energy, food, drinks, exercise, sleep, treatments. Use the health-tracking skill's `form_submit.py` to submit — it fuzzy-matches transcription errors against the form's predefined options.
5. **Always reply** with what you understood and what action you took

## Architecture

```
Voice Memos (iPhone/Watch) → iCloud sync → Mac launchd folder watcher
  → ffmpeg normalize → Parakeet TDT 0.6B + Whisper large-v3-turbo
  → email to Claude (✅ queue) → post-process + take action
```

## Key Files

- `watcher.py` — launchd-triggered script, orchestrates the pipeline
- `vocab_prompt.txt` — medication/medical vocabulary for Whisper prompt conditioning
- `state.json` — tracks processed recordings
- `com.aronhuang.voice-capture.plist` — launchd service definition
