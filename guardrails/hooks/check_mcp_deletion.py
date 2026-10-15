#!/usr/bin/env python3
"""check_mcp_deletion.py - deny Write/Edit/MultiEdit that drops existing `mcpServers` entries.

Risk: a settings edit can silently drop an existing MCP server entry. Rewriting a JSON
config wholesale (Write, or a wide Edit) drops keys easily, and nothing fails at that
moment. You only notice the next time you try to use the server.

How: for writes to config files that can hold `mcpServers`, build the post-edit
content first, then compare the key sets.
  - keys removed            -> deny (names the servers that would disappear)
  - post-edit JSON broken   -> deny (Claude Code itself would fail to load it)
  - keys added / unchanged  -> pass
Files that currently have no `mcpServers` are ignored, so adding a first server is never blocked.

Output never contains values, only key names (config files hold credentials).
"""

import json
import os
import sys

WATCH_BASENAMES = ('.claude.json', 'settings.json', 'settings.local.json', '.mcp.json')


def mcp_keys(text):
    """Set of mcpServers keys, or None if the JSON is broken."""
    try:
        d = json.loads(text)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(d, dict):
        return None
    m = d.get('mcpServers')
    return set(m.keys()) if isinstance(m, dict) else set()


def apply_edit(text, old, new, replace_all):
    if not old:
        return text
    return text.replace(old, new) if replace_all else text.replace(old, new, 1)


def build_after(tool, ti, before_text):
    if tool == 'Write':
        c = ti.get('content')
        return c if isinstance(c, str) else None
    if tool == 'Edit':
        return apply_edit(before_text, ti.get('old_string') or '',
                          ti.get('new_string') or '', bool(ti.get('replace_all')))
    if tool == 'MultiEdit':
        t = before_text
        edits = ti.get('edits')
        if not isinstance(edits, list):
            return None
        for e in edits:
            if not isinstance(e, dict):
                return None
            t = apply_edit(t, e.get('old_string') or '', e.get('new_string') or '',
                           bool(e.get('replace_all')))
        return t
    return None


def deny(reason):
    payload = json.dumps({
        'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': '[rule=mcp-deletion] ' + reason,
        }
    }, ensure_ascii=False)
    # Write bytes: on a cp932/cp1252 console, print() of non-ASCII can raise, the hook
    # crashes, and the deny never arrives (hooks fail open).
    try:
        sys.stdout.buffer.write(payload.encode('utf-8'))
        sys.stdout.buffer.flush()
    except AttributeError:
        sys.stdout.write(payload)
    sys.exit(0)


def main():
    try:
        inp = json.load(sys.stdin)
    except Exception:  # noqa: BLE001
        sys.exit(0)
    if not isinstance(inp, dict):
        sys.exit(0)
    tool = inp.get('tool_name')
    if tool not in ('Write', 'Edit', 'MultiEdit'):
        sys.exit(0)

    ti = inp.get('tool_input') or {}
    path = ti.get('file_path') or ''
    if os.path.basename(path.replace('\\', '/')).lower() not in WATCH_BASENAMES:
        sys.exit(0)
    if not os.path.exists(path):
        sys.exit(0)
    try:
        with open(path, encoding='utf-8') as f:
            before_text = f.read()
    except OSError:
        sys.exit(0)

    before = mcp_keys(before_text)
    if not before:
        sys.exit(0)

    after_text = build_after(tool, ti, before_text)
    if after_text is None:
        sys.exit(0)

    after = mcp_keys(after_text)
    if after is None:
        deny('After this edit {} is no longer valid JSON. The file holds {} mcpServers '
             'entries; a broken file disables all of them. Review the edit.'
             .format(os.path.basename(path), len(before)))

    removed = sorted(before - after)
    if removed:
        deny('This edit removes mcpServers entries: {} (in {}). Keep existing entries when '
             'editing. To remove one on purpose, use `claude mcp remove <name>`.'
             .format(', '.join(removed), os.path.basename(path)))
    sys.exit(0)


if __name__ == '__main__':
    main()
