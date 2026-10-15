# -*- coding: utf-8 -*-
r"""memory-index-repair filecheck - do the files that memories (and handoffs) name actually exist?

scan.py checks index <-> memory body (.md names). This checks a different break:
**a memory body says "the deliverable is at path X". Is it really there?**

Risk it addresses: a project memory or a handoff can say a customer deliverable was
"created and placed in the shared drive" when it exists nowhere. Each later session
trusts the note, so the gap stays hidden until the file is needed. A check that only
looks at .md files cannot see this.

Verdicts:
  ok     ... exists
  GHOST  ... named but missing (highest impact; fix these)
  moved  ... missing at that path, but a file with the same name exists elsewhere under
             --search-root (the path in the memory is stale)
  skip   ... placeholder / wildcard / unresolvable; cannot be judged mechanically

Diagnosis order: when exists() returns False, suspect "the file moved or was deleted"
before suspecting "the filesystem check is broken". On a synced drive a file moved one
folder down can look like a broken check.

Usage:
  python filecheck.py [--memory-dir DIR ...] [--handoff-dir DIR] [--base-dir DIR]
                      [--search-root DIR ...] [--format text|json] [--limit N]
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan import default_memory_dir  # noqa: E402

# Extensions to pick up (.md is scan.py's job)
EXTS = (
    '.pptx', '.pdf', '.xlsx', '.docx', '.csv', '.json', '.py', '.txt',
    '.png', '.jpg', '.zip', '.jsonl', '.ps1', '.js', '.sh',
)
# Code spans first (memories usually write paths as `code`)
CODESPAN = re.compile(r'`([^`\n]+)`')
# Bare absolute / home-relative paths
BARE = re.compile(r'(?:[A-Za-z]:[\\/]|~[\\/]|/)[^\s`|)\]、。）]+')
# Not mechanically checkable
PLACEHOLDER = re.compile(r'[{}<>*?]|YYYY|YYMMDD|MMDD|HHMMSS|\.\.\.|…|\{N\}')


def looks_like_path(s):
    s = s.strip()
    if not s or len(s) > 300:
        return False
    if not s.lower().endswith(EXTS):
        return False
    return '\\' in s or '/' in s


def normalize(p, base_dir):
    """Map a path as written in memory to a real path. None if it cannot be resolved."""
    p = p.strip().strip('"').strip("'").rstrip('。、）)')
    if PLACEHOLDER.search(p):
        return None
    if p.startswith('~'):
        p = str(Path.home()) + p[1:]
    if p.startswith('memory/') or p.startswith('memory\\'):
        return None  # .md side is scan.py's job
    if not os.path.isabs(p) and not re.match(r'^[A-Za-z]:[\\/]', p):
        if not base_dir:
            return None
        p = os.path.join(base_dir, p)
    return os.path.normpath(p)


_name_index = None


def build_name_index(roots):
    """basename -> [parent dirs], built once from --search-root."""
    idx = {}
    for root in roots:
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                idx.setdefault(f, []).append(dirpath)
    return idx


def collect(paths, sources, text, src):
    for rx in (CODESPAN, BARE):
        for m in rx.finditer(text):
            cand = m.group(1) if rx is CODESPAN else m.group(0)
            if looks_like_path(cand):
                paths.add(cand)
                sources.setdefault(cand, set()).add(src)


def main():
    global _name_index
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='Check that file paths named in memories exist')
    ap.add_argument('--memory-dir', action='append', default=None)
    ap.add_argument('--handoff-dir', default=None, help='also scan the newest 30 *.txt/*.md handoffs here')
    ap.add_argument('--base-dir', default=str(Path.home()),
                    help='base for relative paths (default: home directory)')
    ap.add_argument('--search-root', action='append', default=[],
                    help='directory to search by file name to detect "moved" (repeatable)')
    ap.add_argument('--format', default='text', choices=['text', 'json'])
    ap.add_argument('--limit', type=int, default=0, help='max items to print per class (0 = all)')
    args = ap.parse_args()

    raw, sources = set(), {}
    for d in args.memory_dir or [default_memory_dir()]:
        if not os.path.isdir(d):
            print(f'memory dir not found: {d}')
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith('.md'):
                with open(os.path.join(d, fn), encoding='utf-8', errors='replace') as fh:
                    collect(raw, sources, fh.read(), f'memory:{fn}')
    if args.handoff_dir and os.path.isdir(args.handoff_dir):
        hs = sorted((f for f in os.listdir(args.handoff_dir) if f.endswith(('.txt', '.md'))),
                    reverse=True)[:30]
        for fn in hs:
            with open(os.path.join(args.handoff_dir, fn), encoding='utf-8', errors='replace') as fh:
                collect(raw, sources, fh.read(), f'handoff:{fn}')

    if args.search_root:
        _name_index = build_name_index(args.search_root)

    results = {'ok': [], 'ghost': [], 'moved': [], 'skip': []}
    for cand in sorted(raw):
        p = normalize(cand, args.base_dir)
        srcs = sorted(sources.get(cand, []))
        if p is None:
            results['skip'].append({'path': cand, 'sources': srcs})
        elif os.path.exists(p):
            results['ok'].append({'path': p, 'sources': srcs})
        elif _name_index and os.path.basename(p) in _name_index:
            results['moved'].append({'path': p, 'found_in': _name_index[os.path.basename(p)][:5],
                                     'sources': srcs})
        else:
            results['ghost'].append({'path': p, 'sources': srcs})

    if args.format == 'json':
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    total = sum(len(v) for v in results.values())
    print(f'extracted {total} / ok {len(results["ok"])} / GHOST {len(results["ghost"])} / '
          f'moved {len(results["moved"])} / skip {len(results["skip"])}')
    print()
    for key, label in (('ghost', 'GHOST - named but does not exist'),
                       ('moved', 'moved - not at that path, same name found elsewhere')):
        items = results[key]
        if not items:
            continue
        print(f'{label}  ({len(items)})')
        for it in (items[:args.limit] if args.limit else items):
            print(f'   {it["path"]}')
            if key == 'moved':
                print(f'     -> found in: {", ".join(it["found_in"])}')
            print(f'     cited by: {", ".join(it["sources"])}')
        if args.limit and len(items) > args.limit:
            print(f'   ... {len(items) - args.limit} more')
        print()
    if not results['ghost'] and not results['moved']:
        print('No breaks. Every file named by memory exists.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
