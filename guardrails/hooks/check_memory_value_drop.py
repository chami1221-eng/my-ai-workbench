#!/usr/bin/env python3
"""check_memory_value_drop.py - stop money amounts, stages and record IDs from silently
disappearing from agent memory notes (PreToolUse: Write / Edit / MultiEdit).

Rule being enforced: when a value changes, do not overwrite it. Keep the old value and
mark it, e.g. `[INVALIDATED 2026-09-28] old 5.76M -> new 1.44M`, or `[ERRATUM ...] old -> correct`.
Overwriting loses "what changed when", and stale copies of the old value in index
files can no longer be detected.

Passes when:
  - the value is still present after the edit (moved within the same file)
  - the newly written text contains an invalidation marker
  - every line holding the value already exists in another note of the same memory
    directory (the note was archived / garbage-collected elsewhere)
Scope: files named project_* / feedback_* / reference_* (*.md) under MEMORY_DIRS.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard_config as C  # noqa: E402
from memory_values import values  # noqa: E402


def zone_of(path):
    p = os.path.normcase(os.path.abspath(path))
    for d in C.MEMORY_DIRS:
        d = os.path.normcase(os.path.abspath(str(d)))
        if p.startswith(d + os.sep):
            return d
    return None


def after_text(tool, ti, before):
    if tool == "Write":
        return ti.get("content") or "", ti.get("content") or ""
    edits = ti.get("edits") if tool == "MultiEdit" else [ti]
    text, added = before, ""
    for e in edits or []:
        old, new = e.get("old_string") or "", e.get("new_string") or ""
        if e.get("replace_all"):
            text = text.replace(old, new)
        else:
            text = text.replace(old, new, 1)
        added += new
    return text, added


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    tool = payload.get("tool_name") or ""
    if tool not in ("Write", "Edit", "MultiEdit"):
        return 0
    ti = payload.get("tool_input") or {}
    path = ti.get("file_path") or ""
    name = os.path.basename(path)
    zone = zone_of(path) if path else None
    if not zone or not name.startswith(C.MEMORY_PREFIXES) or not name.endswith(".md"):
        return 0
    if not os.path.exists(path):
        return 0   # new file: nothing can be lost
    with open(path, encoding="utf-8") as f:
        before = f.read()
    after, added = after_text(tool, ti, before)
    if any(m in added for m in C.INVALIDATION_MARKS):
        return 0

    after_vals = values(after)
    lost = {k: raw for k, raw in values(before).items() if k not in after_vals}
    if not lost:
        return 0

    # Archived elsewhere? Compare whole lines, not bare values: matching only the value
    # let common values (e.g. "Stage 1") or amounts mentioned in other notes slip through.
    others = ""
    for fn in os.listdir(zone):
        if fn.endswith(".md") and fn != name:
            try:
                with open(os.path.join(zone, fn), encoding="utf-8") as f:
                    others += f.read()
            except Exception:
                pass

    def moved(raw):
        lines = [l.strip() for l in before.splitlines() if raw in l and len(l.strip()) >= 20]
        return bool(lines) and all(l in others for l in lines)

    lost = {k: raw for k, raw in lost.items() if not moved(raw)}
    if not lost:
        return 0

    shown = ", ".join(list(lost.values())[:6])
    reason = (
        f"[rule=memory-value-drop] This edit removes values from {name}: {shown}. "
        "Do not delete old values. Keep them with a marker: "
        "`[INVALIDATED YYYY-MM-DD] old -> new` (value changed) or "
        "`[ERRATUM YYYY-MM-DD] old -> correct` (original was wrong). "
        "That keeps the change history and lets index linting find stale copies."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
