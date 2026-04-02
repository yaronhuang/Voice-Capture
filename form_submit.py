#!/usr/bin/env python3
"""Submit entries to Aaron's Life Tracking Google Form.

Usage:
    # List all categories
    python form_submit.py categories

    # List fields and options for a category
    python form_submit.py options "💊 Medications and Supplements"

    # Find closest matching option (fuzzy match)
    python form_submit.py match "💊 Medications" "tylenol 500"

    # Submit a form entry (JSON)
    python form_submit.py submit '{
        "category": "💊 Medications and Supplements",
        "💊 Medications": ["Synthroid 75 mcg"],
        "💊 Supplements": ["Centrum Mens 50 1 tab"]
    }'

    # Submit with topical medications (grid)
    python form_submit.py submit '{
        "category": "💊 Medications and Supplements",
        "💊 Medications": ["Synthroid 75 mcg"],
        "topical": {"Clindamycin": ["Face", "Neck"], "Tretinoin 0.1%": ["Face"]}
    }'

    # Dry run (validate without submitting)
    python form_submit.py submit --dry-run '{ ... }'
"""

from __future__ import annotations

import difflib
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "form_config.json"


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text())


def fuzzy_match(query: str, options: list[str], cutoff: float = 0.4) -> str | None:
    """Find the closest matching option. Tries exact substring first, then difflib."""
    q = query.lower().strip()

    # Exact match (case-insensitive, ignoring leading emoji)
    for opt in options:
        opt_stripped = opt.lstrip("💊🥣🧂🥛🥤☕🍵🫚🍫🌿🍵🌶️🥬🥗🫐🍇🍄🥦🍎🍋🥥⚡❤️🍱👍🤔👎😃🏃💉☢️🧲🔉🧪🩻📖🙇🎧🪴🐈🧹😲😛🤐👀🦷👂🪭🌲🐱📺💻🏋️‍♀️ ").strip()
        if q == opt_stripped.lower() or q == opt.lower():
            return opt

    # Substring match
    for opt in options:
        if q in opt.lower():
            return opt

    # Fuzzy match via difflib
    # Compare against stripped (no-emoji) versions but return the original
    stripped_map = {}
    for opt in options:
        stripped = opt.lower()
        stripped_map[stripped] = opt

    matches = difflib.get_close_matches(q, stripped_map.keys(), n=1, cutoff=cutoff)
    if matches:
        return stripped_map[matches[0]]

    return None


def cmd_categories(config: dict):
    print(json.dumps(config["categories"], indent=2, ensure_ascii=False))


def cmd_options(config: dict, category: str):
    # Fuzzy match category name
    cat = fuzzy_match(category, config["categories"]) or category
    fields = {k: v for k, v in config["fields"].items() if v["category"] == cat}

    if not fields:
        print(json.dumps({"error": f"No fields found for category '{category}'", "categories": config["categories"]}))
        sys.exit(1)

    result = {"category": cat, "fields": {}}
    for name, field in fields.items():
        entry = {"entry_id": field["entry_id"], "type": field["type"]}
        if "options" in field:
            entry["options"] = field["options"]
        result["fields"][name] = entry

    # Include topical grid if this is medications
    if "Medications" in cat:
        result["topical_grid"] = config["topical_grid"]

    print(json.dumps(result, indent=2, ensure_ascii=False))


def cmd_match(config: dict, field_name: str, query: str):
    # Find the field
    field = None
    for name, f in config["fields"].items():
        if field_name.lower() in name.lower():
            field = f
            field_name = name
            break

    if not field or "options" not in field:
        # Try topical grid
        grid = config.get("topical_grid", {})
        meds = list(grid.get("entry_ids", {}).keys())
        if meds:
            result = fuzzy_match(query, meds)
            if result:
                print(json.dumps({"field": "Topical Medication", "matched": result, "query": query}))
                return
        print(json.dumps({"error": f"Field '{field_name}' not found or has no options"}))
        sys.exit(1)

    result = fuzzy_match(query, field["options"])
    if result:
        print(json.dumps({"field": field_name, "query": query, "matched": result}))
    else:
        print(json.dumps({"field": field_name, "query": query, "matched": None, "options": field["options"]}))
        sys.exit(1)


def cmd_submit(config: dict, data: dict, dry_run: bool = False):
    category = data.get("category")
    if not category:
        print(json.dumps({"error": "Missing 'category' key"}))
        sys.exit(1)

    # Fuzzy match category
    matched_cat = fuzzy_match(category, config["categories"])
    if not matched_cat:
        print(json.dumps({"error": f"Unknown category '{category}'", "categories": config["categories"]}))
        sys.exit(1)

    # Build form data — list of (key, value) tuples to handle multi-select
    form_data: list[tuple[str, str]] = []
    form_data.append((f"entry.{config['category_entry_id']}", matched_cat))

    # Get valid fields for this category
    cat_fields = {k: v for k, v in config["fields"].items() if v["category"] == matched_cat}
    warnings = []

    for key, value in data.items():
        if key in ("category", "topical"):
            continue

        # Find matching field
        matched_field = None
        for fname, fdef in cat_fields.items():
            if key == fname or key.lower() in fname.lower():
                matched_field = (fname, fdef)
                break

        if not matched_field:
            warnings.append(f"Unknown field '{key}' for category '{matched_cat}'")
            continue

        fname, fdef = matched_field
        entry_key = f"entry.{fdef['entry_id']}"

        if fdef["type"] == "text" or fdef["type"] == "paragraph":
            form_data.append((entry_key, str(value)))
        elif fdef["type"] in ("single", "multi"):
            values = value if isinstance(value, list) else [value]
            options = fdef.get("options", [])
            for v in values:
                if options:
                    matched_opt = fuzzy_match(str(v), options)
                    if matched_opt:
                        form_data.append((entry_key, matched_opt))
                    else:
                        warnings.append(f"No match for '{v}' in {fname}")
                else:
                    form_data.append((entry_key, str(v)))

    # Handle topical medication grid
    topical = data.get("topical", {})
    if topical:
        grid = config.get("topical_grid", {})
        entry_ids = grid.get("entry_ids", {})
        valid_areas = grid.get("body_areas", [])

        for med_name, areas in topical.items():
            matched_med = fuzzy_match(med_name, list(entry_ids.keys()))
            if not matched_med:
                warnings.append(f"Unknown topical medication '{med_name}'")
                continue
            eid = entry_ids[matched_med]
            area_list = areas if isinstance(areas, list) else [areas]
            for area in area_list:
                matched_area = fuzzy_match(area, valid_areas)
                if matched_area:
                    form_data.append((f"entry.{eid}", matched_area))
                else:
                    warnings.append(f"Unknown body area '{area}' for {matched_med}")

    if dry_run:
        result = {
            "dry_run": True,
            "category": matched_cat,
            "form_data": form_data,
        }
        if warnings:
            result["warnings"] = warnings
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    fields_submitted = len(form_data) - 1  # minus category field

    # Add pageHistory (required for multi-page forms) and fvv
    page_history = config.get("page_history", {}).get(matched_cat, "0")
    form_data.append(("pageHistory", page_history))
    form_data.append(("fvv", "1"))

    # POST to Google Forms
    encoded = urllib.parse.urlencode(form_data)
    req = urllib.request.Request(
        config["form_url"],
        data=encoded.encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        resp = urllib.request.urlopen(req, timeout=15)
        status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code

    # Google Forms returns 200 on success (shows confirmation page)
    success = status == 200
    result = {
        "success": success,
        "status": status,
        "category": matched_cat,
        "fields_submitted": fields_submitted,
    }
    if warnings:
        result["warnings"] = warnings
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if not success:
        sys.exit(1)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    config = load_config()
    cmd = sys.argv[1]

    if cmd == "categories":
        cmd_categories(config)

    elif cmd == "options":
        if len(sys.argv) < 3:
            print("Usage: form_submit.py options <category>")
            sys.exit(1)
        cmd_options(config, sys.argv[2])

    elif cmd == "match":
        if len(sys.argv) < 4:
            print("Usage: form_submit.py match <field> <query>")
            sys.exit(1)
        cmd_match(config, sys.argv[2], sys.argv[3])

    elif cmd == "submit":
        dry_run = "--dry-run" in sys.argv
        args = [a for a in sys.argv[2:] if a != "--dry-run"]
        if not args:
            print("Usage: form_submit.py submit [--dry-run] '<json>'")
            sys.exit(1)
        data = json.loads(args[0])
        cmd_submit(config, data, dry_run=dry_run)

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
