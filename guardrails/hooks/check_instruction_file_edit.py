#!/usr/bin/env python3
"""check_instruction_file_edit.py - turn Edit/Write/MultiEdit on protected instruction
files (default: ~/.claude/CLAUDE.md) into an "ask".

Why: shared rules (identity, language, output locations, security) were moved to one
canonical file read by several agents (Claude Code imports it, Codex reads it
natively). Writing shared rules back into CLAUDE.md revives double maintenance, so
direct edits pause for a human click.

Not deny: agent-specific sections still get legitimate edits; "ask" keeps that path open.
Shell-based writes to the same files are covered by bash_guard.py (instruction-file-write).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard_config as C  # noqa: E402


def _norm(p):
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(p))))


def main():
    try:
        inp = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if not isinstance(inp, dict) or inp.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        sys.exit(0)

    path = ((inp.get("tool_input", {}) or {}).get("file_path", "") or "")
    if not path:
        sys.exit(0)
    targets = {_norm(t) for t in C.ASK_EDIT_FILES}
    if _norm(path) in targets:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": (
                    "[rule=instruction-file-edit] Direct edit of %s. Shared rules live in %s; "
                    "edit that file instead. If this is an agent-specific section, approve and continue."
                    % (os.path.basename(path), C.CANONICAL_RULES_FILE)),
            }
        }, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
