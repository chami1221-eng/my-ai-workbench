# -*- coding: utf-8 -*-
"""Break down the prompt prefix (input tokens of the first turn) of a Claude Code session.

Bytes alone do not tell you what to cut. This reads the first assistant turn's `usage` from the
session transcript (the whole prefix: system prompt, tool list, skill list, always-on files)
and splits it into three parts using numbers you measured separately:

  floor     : Claude Code's own minimum. Measure once by starting a session in safe mode
              (no hooks, no CLAUDE.md, no skills) and running this script on it.
  injected  : always-on text you control (CLAUDE.md, rules, MEMORY.md, hook injections).
  remainder : prefix - floor - injected  ~= skill list + deferred tool names.

Usage:
  python prefix_baseline.py                      # $CLAUDE_CODE_SESSION_ID, else newest transcript
  python prefix_baseline.py <session-id>
  python prefix_baseline.py --floor 25625 --injected 39369 --previous 91072

Take a baseline BEFORE changing anything (turning skills off, removing MCP servers, upgrading),
change one thing, start a NEW session, and measure again. The skill list is baked into the
prefix, so changes do not show up in the session where you made them.
"""
import argparse
import glob
import json
import os
import sys
from pathlib import Path


def pick_session(root, sid_arg):
    for sid, how in ((sid_arg, 'argument'), (os.environ.get('CLAUDE_CODE_SESSION_ID', ''), '$CLAUDE_CODE_SESSION_ID')):
        if sid:
            hits = glob.glob(os.path.join(root, '*', sid + '.jsonl'))
            if hits:
                return hits[0], f'{how} {sid}'
    files = glob.glob(os.path.join(root, '*', '*.jsonl'))
    if not files:
        return None, 'no transcript found'
    p = max(files, key=os.path.getmtime)
    return p, f'newest transcript {os.path.basename(p)[:-6]}'


def first_turn(path):
    """usage of the first assistant message = that turn's prefix."""
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get('type') != 'assistant':
                continue
            msg = d.get('message') or {}
            u = msg.get('usage') or {}
            if u:
                return u, msg.get('model', '?')
    return None, None


def skill_list_estimate(skills_dir):
    rows = []
    for p in glob.glob(os.path.join(skills_dir, '*', 'SKILL.md')):
        name = os.path.basename(os.path.dirname(p))
        with open(p, encoding='utf-8', errors='replace') as fh:
            head = fh.read(4000)
        desc = ''
        for line in head.splitlines():
            if line.lower().startswith('description:'):
                desc = line.split(':', 1)[1].strip()
                break
        rows.append(len(name) + len(desc))
    return len(rows), sum(rows)


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='Break down the first-turn prompt prefix of a Claude Code session.')
    ap.add_argument('session', nargs='?', default='', help='session id (default: current or newest)')
    ap.add_argument('--projects-root', default=str(Path.home() / '.claude' / 'projects'))
    ap.add_argument('--skills-dir', default=str(Path.home() / '.claude' / 'skills'))
    ap.add_argument('--floor', type=int, default=0, help='safe-mode prefix tokens (Claude Code minimum)')
    ap.add_argument('--injected', type=int, default=0, help='always-on injected tokens you control')
    ap.add_argument('--previous', type=int, default=0, help='previous baseline prefix, to print the delta')
    a = ap.parse_args()

    path, how = pick_session(a.projects_root, a.session)
    print(f'== Session\n  {how}')
    if not path:
        return 1
    u, model = first_turn(path)
    print(f'\n== First-turn prefix (model={model})')
    if not u:
        print('  no usage found')
        return 1
    inp = u.get('input_tokens', 0)
    cc = u.get('cache_creation_input_tokens', 0)
    cr = u.get('cache_read_input_tokens', 0)
    prefix = inp + cc + cr
    for k, v in (('input', inp), ('cache_creation', cc), ('cache_read', cr)):
        print(f'  {k:<16} {v:>9,}')
    print(f'  {"prefix total":<16} {prefix:>9,} tok')

    if a.floor or a.injected:
        print('\n== Breakdown')
        print(f'  Claude Code floor (safe mode)   {a.floor:>9,}  (cannot be reduced from your side)')
        print(f'  always-on injection             {a.injected:>9,}')
        print(f'  remainder (skills + tool names) {prefix - a.floor - a.injected:>9,}')
    if a.previous:
        print(f'\n  vs previous baseline {a.previous:,} -> {prefix:,} ({prefix - a.previous:+,})')

    n, chars = skill_list_estimate(a.skills_dir)
    print(f'\n== Skill list size ({a.skills_dir})')
    print(f'  {n} skills / name+description {chars:,} chars')
    print('  Note: cutting skills does not always shrink this. In Codex CLI, 152 -> 51 skills saved only'
          ' 460 tok because each remaining description grew (a budget-style allocation is suspected).'
          ' Measure before and after.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
