# -*- coding: utf-8 -*-
"""memory-index-repair scan - detect breaks between the memory index and memory bodies,
in BOTH directions.

A freshness scan only asks "is this memory old?". It never asks "does the body exist?"
or "can the index reach it?". Comparing the two as sets once showed 284 names in the
index, 206 body files, and only 165 (51%) present in both.

Two directions:
  orphan  ... body exists, no index line points to it -> never auto-recalled (effectively forgotten)
  ghost   ... index line exists, body does not        -> "details in X.md" points to nothing

Ghosts are not equally harmful. A ghost named by a file that is injected on every turn
(MEMORY.md itself, an always-on rules file, CLAUDE.md, layer0.md) misfires on every turn.
Those are reported first.

Usage:
  python scan.py [--memory-dir DIR ...] [--cite-file FILE ...] [--cite-dir DIR ...]
                 [--nonghost FILE] [--format text|json]

Defaults:
  --memory-dir : $CLAUDE_MEMORY_DIR or ~/.claude/projects/<cwd-key>/memory
  --cite-file  : ./CLAUDE.md, ~/.claude/CLAUDE.md and <memory-dir>/layer0.md if they exist
  --cite-dir   : ./.claude/rules if it exists
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path


def default_memory_dir():
    env = os.environ.get('CLAUDE_MEMORY_DIR')
    if env:
        return env
    key = re.sub(r'[^A-Za-z0-9]', '-', str(Path.cwd()))
    return str(Path.home() / '.claude' / 'projects' / key / 'memory')


def is_index(fn):
    """Index files are not memories themselves. layer0.md is always-on rules, not an index."""
    return fn == 'MEMORY.md' or fn.startswith('index_') or fn == 'layer0.md'


# Names in index text: markdown links "(xxx.md)" and wikilinks "[[xxx]]"
LINK_MD = re.compile(r'\(([A-Za-z0-9_\-]+)\.md\)')
LINK_WIKI = re.compile(r'\[\[([A-Za-z0-9_\-]+)\]\]')
# Citations from always-on files. Memory bodies always start with one of these prefixes.
# Without the prefix restriction, CLAUDE.md / SKILL.md / rules files were reported as
# ghosts (7 false positives measured).
CITE = re.compile(r'((?:feedback|project|reference|index|user)_[A-Za-z0-9_\-]+)\.md')


def read(path, n=None):
    with open(path, encoding='utf-8', errors='replace') as fh:
        return fh.read(n) if n else fh.read()


def load_zone(path):
    if not os.path.isdir(path):
        return None
    files = [f for f in os.listdir(path) if f.endswith('.md')]
    bodies = {f[:-3] for f in files if not is_index(f)}
    # Stubs (bodies rebuilt from index lines when no body was found) are counted separately.
    # Without this the report says "matched 100%" while the content is still missing.
    stubs = set()
    for f in files:
        if is_index(f):
            continue
        if 'reconstructed:' in read(os.path.join(path, f), 1500):
            stubs.add(f[:-3])
    idx_text = ''
    hot_text = ''
    for f in files:
        if f == 'layer0.md' or not is_index(f):
            continue
        t = read(os.path.join(path, f))
        idx_text += t + '\n'
        # MEMORY.md is injected every turn. A ghost here misfires every turn.
        if f == 'MEMORY.md':
            hot_text = t
    linked = set(LINK_MD.findall(idx_text)) | set(LINK_WIKI.findall(idx_text))
    linked = {n for n in linked if not is_index(n + '.md')}
    hot = set(LINK_MD.findall(hot_text)) | set(LINK_WIKI.findall(hot_text))
    hot = {n for n in hot if not is_index(n + '.md')}
    return {'path': path, 'bodies': bodies, 'linked': linked, 'stubs': stubs,
            'hot': hot}


def load_nonghost(path):
    """Allowlist of names that appear but correctly have no body
    (for example "bad example" file names quoted in a rules document)."""
    if not path or not os.path.exists(path):
        return set()
    out = set()
    for line in read(path).splitlines():
        line = line.split('#', 1)[0].strip()
        if line:
            out.add(line[:-3] if line.endswith('.md') else line)
    return out


def citation_sources(args, memdirs):
    files = list(args.cite_file or [])
    dirs = list(args.cite_dir or [])
    if not args.cite_file and not args.cite_dir:
        files += [str(Path.cwd() / 'CLAUDE.md'), str(Path.home() / '.claude' / 'CLAUDE.md')]
        rules = Path.cwd() / '.claude' / 'rules'
        if rules.is_dir():
            dirs.append(str(rules))
    for md in memdirs:
        files.append(os.path.join(md, 'layer0.md'))
    for d in dirs:
        if os.path.isdir(d):
            files += [os.path.join(d, f) for f in os.listdir(d) if f.endswith('.md')]
    return [f for f in files if os.path.exists(f)]


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='Detect orphans and ghost links between memory index and bodies')
    ap.add_argument('--memory-dir', action='append', default=None,
                    help='memory directory (repeatable for multiple zones)')
    ap.add_argument('--cite-file', action='append', help='always-on file that may cite memories (repeatable)')
    ap.add_argument('--cite-dir', action='append', help='directory of always-on *.md files (repeatable)')
    ap.add_argument('--nonghost', default=None,
                    help='allowlist file (default: nonghost.txt next to this skill, if present)')
    ap.add_argument('--format', choices=['text', 'json'], default='text')
    a = ap.parse_args()

    memdirs = a.memory_dir or [default_memory_dir()]
    nonghost_path = a.nonghost or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'nonghost.txt')
    nonghost = load_nonghost(nonghost_path)

    cited = set()
    for f in citation_sources(a, memdirs):
        cited |= set(CITE.findall(read(f)))
    cited = {n for n in cited if not is_index(n + '.md')}

    zones = {md: load_zone(md) for md in memdirs}
    # Resolve citations across all zones (an always-on file may point to another zone).
    all_bodies = set()
    for z in zones.values():
        if z:
            all_bodies |= z['bodies']

    result = {}
    for md, z in zones.items():
        if z is None:
            print(f'memory dir not found: {md}')
            continue
        orphans = sorted(z['bodies'] - z['linked'])
        ghosts = sorted(g for g in z['linked'] - z['bodies'] if g not in nonghost)
        matched = sorted(z['bodies'] & z['linked'])
        # Named by an always-on file but in no index and with no body.
        # Index-based ghost detection can never see this class.
        cited_only = sorted(n for n in cited
                            if n not in all_bodies and n not in z['linked'] and n not in nonghost)
        critical = sorted({g for g in ghosts if g in cited or g in z['hot']} | set(cited_only))
        result[md] = {
            'bodies': len(z['bodies']), 'linked': len(z['linked']), 'matched': len(matched),
            'orphans': orphans, 'ghosts': ghosts,
            'ghosts_injected_every_turn': critical,
            'ghosts_cited_only': cited_only,
            'ghosts_in_hot': sorted(g for g in ghosts if g in z['hot']),
            'stubs': sorted(z['stubs']),
            'stubs_matched': len([m for m in matched if m in z['stubs']]),
            'substantive': len([m for m in matched if m not in z['stubs']]),
        }

    if a.format == 'json':
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    for md, r in result.items():
        total = len(set(r['orphans']) | set(r['ghosts'])) + r['matched']
        pct = (r['matched'] / total * 100) if total else 100.0
        print(f'=== {md} ===')
        print(f'  bodies {r["bodies"]} / indexed names {r["linked"]} / matched {r["matched"]} ({pct:.0f}%)')
        # "matched" only means a file exists. Stubs have no recovered content.
        if r['stubs_matched']:
            spct = r['substantive'] / r['matched'] * 100 if r['matched'] else 100.0
            print(f'    of which stubs (rebuilt from index lines, content not recovered): '
                  f'{r["stubs_matched"]}  -> bodies with real content: {r["substantive"]} ({spct:.0f}%)')
        print()
        print(f'  GHOST links (in index, no body): {len(r["ghosts"])}')
        if r['ghosts_injected_every_turn']:
            print(f'    * named by MEMORY.md / layer0 / CLAUDE.md / rules = '
                  f'injected every turn but points to nothing: {len(r["ghosts_injected_every_turn"])}')
            for g in r['ghosts_injected_every_turn']:
                tag = ' [HOT]' if g in r['ghosts_in_hot'] else ''
                tag += ' [cited only, not in any index]' if g in r['ghosts_cited_only'] else ''
                print(f'      - {g}.md{tag}')
        other = [g for g in r['ghosts'] if g not in r['ghosts_injected_every_turn']]
        for g in other[:20]:
            print(f'      - {g}.md')
        if len(other) > 20:
            print(f'      ... {len(other) - 20} more')
        print()
        print(f'  ORPHANS (body exists, no index line = never auto-recalled): {len(r["orphans"])}')
        for o in r['orphans'][:20]:
            print(f'      - {o}.md')
        if len(r['orphans']) > 20:
            print(f'      ... {len(r["orphans"]) - 20} more')
        print()
    print('Repair policy: see SKILL.md. For ghosts, search for the body first; deleting the index line is the last resort.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
