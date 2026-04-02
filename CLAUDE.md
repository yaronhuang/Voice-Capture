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
4. **Take action** — prefer submitting to the Life Tracking form whenever the content matches a form category:
   - Medication/supplement log → submit via `form_submit.py` (category: `💊 Medications and Supplements`)
   - Symptom/energy/mood report → submit via `form_submit.py` (category: `😃 Symptoms`)
   - Food/drink intake → submit via `form_submit.py` (category: `🍱 Food` or `🥤 Drinks`)
   - Exercise → submit via `form_submit.py` (category: `🏋️‍♀️ Exercise`)
   - Sleep data → submit via `form_submit.py` (category: `⏾ Sleep`)
   - Treatment/imaging → submit via `form_submit.py` (category: `🏥 Treatments`)
   - Journal entry (not trackable) → save to the appropriate project
   - Question → research and reply
   - Unclear → ask Aaron to confirm
5. **Always reply** with what you understood and what action you took

## Life Tracking Form Submission

Submit structured data to Aaron's Google Form using `form_submit.py`. The form's predefined
options constrain the values — use fuzzy matching to map Aaron's speech to exact form options.

### How to submit

```bash
python Voice-Capture/form_submit.py submit '{ "category": "...", "field": ["value", ...] }'
```

### Useful commands

```bash
# List all categories
python form_submit.py categories

# List fields and valid options for a category
python form_submit.py options "💊 Medications and Supplements"

# Fuzzy match a value against a field's options
python form_submit.py match "💊 Medications" "tylenol 500"

# Dry run (validate without submitting)
python form_submit.py submit --dry-run '{ ... }'
```

### Matching voice transcriptions to form options

Aaron's speech is affected by radiation therapy. Transcription errors are common.
The form's predefined options are the constraint — match against them, not free text.

1. Interpret the transcription (cross-reference both models)
2. Use `form_submit.py match <field> <query>` to find the closest option
3. Submit with the matched value

Example: "Tyler five hundred" → `match "Medications" "tylenol 500"` → `Tylenol 500 mg`

### Field name shortcuts

Field names are matched by substring, so you can use short names:

| Short key | Matches field |
|-----------|---------------|
| `energy` | ⚡️ How's my energy? |
| `How do I feel` | 😊 How do I feel? |
| `Medications` | 💊 Medications |
| `Supplements` | 💊 Supplements |
| `Food` | 🍱 Food |
| `Coffee` | ☕️ Coffee |
| `Exercise` | 🏋️‍♀️ Exercise |
| `Daily Summary` | 📝 Daily Summary |

### Topical medications (grid field)

```json
{
  "category": "💊 Medications and Supplements",
  "topical": {"Clindamycin": ["Face", "Neck"], "Tretinoin 0.1%": ["Face"]}
}
```

### Configuration

- `form_config.json` — all entry IDs, options, and page mappings (auto-generated from form)
- If Aaron adds new options to the form, re-extract with the config generator

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
