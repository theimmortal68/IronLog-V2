#!/usr/bin/env python3
"""Apply reviewed muscle tags to ironlog/seed.py MOVEMENTS dicts.

Reads proposals from scripts/muscle_tags_proposed.json, applies the six
user overrides, then inserts primary_muscle / secondary_muscles into each
dict(...) entry in MOVEMENTS. Idempotent: skips entries that already have
primary_muscle set.

Run from the repo root:
    python scripts/apply_muscle_tags.py
"""
import json
import re
from pathlib import Path

# --- load proposals ---
proposals_path = Path("scripts/muscle_tags_proposed.json")
proposals = json.loads(proposals_path.read_text())
tags = {p["name"]: (p["proposed_primary"], p["proposed_secondary"]) for p in proposals}

# --- apply six user overrides ---
OVERRIDES = {
    "EZ Bar Curl - Narrow Grip [EZ]":   ("BICEPS",          ["FOREARMS"]),
    "Swiss Bar Press [SB]":              ("MID_LOWER_CHEST", ["TRICEPS", "FRONT_DELT"]),
    "Cable Tibialis Raise":              ("TIBIALIS",        []),
    "Cable External Rotation [FT]":      ("ROTATOR_CUFF",    ["REAR_DELT"]),
    "Sumo DL [PB]":                      ("GLUTES",          ["ADDUCTORS", "QUADS", "SPINAL_ERECTORS", "HAMSTRINGS"]),
    "Conventional DL [PB]":              ("GLUTES",          ["HAMSTRINGS", "SPINAL_ERECTORS", "QUADS"]),
}
tags.update(OVERRIDES)

# --- verify every override name is actually in the proposals (fail-fast) ---
proposal_names = {p["name"] for p in proposals}
for override_name in OVERRIDES:
    if override_name not in proposal_names:
        raise ValueError(f"Override name not found in proposals: {override_name!r}")

# --- read seed.py ---
seed_path = Path("ironlog/seed.py")
text = seed_path.read_text()

tagged_count = 0
skipped_count = 0

for name, (primary, secondary) in tags.items():
    # Idempotent check: skip if primary_muscle already present in this dict block
    esc_name = re.escape(name)
    # Find the start of this movement's dict
    start_pattern = re.compile(r'dict\(name="' + esc_name + r'",')
    start_match = start_pattern.search(text)
    if start_match is None:
        raise ValueError(f"Movement name not found in seed.py: {name!r}")

    # Find the end of the dict by tracking paren depth (balanced parens work even
    # inside strings like notes="...(0)..." because they are balanced there too)
    pos = start_match.start()
    depth = 0
    end_pos = None
    for i, ch in enumerate(text[pos:]):
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth == 0:
                end_pos = pos + i
                break

    if end_pos is None:
        raise ValueError(f"Could not find closing ) for movement: {name!r}")

    # Extract the dict block text
    block = text[pos:end_pos + 1]

    # Idempotent: skip if already tagged
    if 'primary_muscle=' in block:
        skipped_count += 1
        continue

    # Insert muscle keys just before the closing )
    sec_json = json.dumps(secondary)
    insertion = f', primary_muscle="{primary}", secondary_muscles={sec_json}'
    text = text[:end_pos] + insertion + text[end_pos:]
    tagged_count += 1

seed_path.write_text(text)
print(f"Done. Tagged: {tagged_count}, skipped (already tagged): {skipped_count}")
print(f"Total movements in proposals: {len(tags)}")
