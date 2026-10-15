# -*- coding: utf-8 -*-
"""Collect the material needed to write index lines for orphan memories, on one screen.

Reading 40 orphans one by one is expensive, so this prints only the frontmatter
(name / type / description) and the first body line, plus a suggested sub-index.
The final call (wording as a trigger, which index) stays with the human or the agent.
Index lines are written as WHEN-to-open triggers, not summaries.

Usage:
  python propose.py [--memory-dir DIR] [--routes routes.json] [--limit N]

routes.json (optional) maps sub-index file names to keyword lists, for example:
  {"index_feedback_ops.md": ["hook", "agent", "browser"],
   "index_feedback_comms.md": ["email", "chat", "calendar"]}
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan import default_memory_dir, load_zone  # noqa: E402

# Generic default routing. Replace with --routes for your own sub-index layout.
DEFAULT_ROUTES = {
    'index_feedback_crm.md': ['crm', 'opportunity', 'forecast', 'quote', 'pricing', 'stage',
                              'renewal', 'sku', 'license'],
    'index_feedback_comms.md': ['mail', 'email', 'minutes', 'notes', 'transcript', 'chat',
                                'calendar', 'meeting', 'draft', 'reply'],
    'index_feedback_deliverables.md': ['pptx', 'slide', 'deck', 'proposal', 'writing',
                                       'document', 'publish', 'share'],
    'index_feedback_ops.md': ['hook', 'claude', 'codex', 'browser', 'scrap', 'agent',
                              'memory', 'skill', 'batch', 'research', 'windows', 'shell'],
}
FM = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.S)


def meta(path):
    try:
        with open(path, encoding='utf-8', errors='replace') as fh:
            t = fh.read()
    except OSError:
        return {}, ''
    t = t.replace('\r\n', '\n')
    m = FM.match(t)
    d = {}
    if m:
        for line in m.group(1).split('\n'):
            if ':' in line and not line.startswith(' '):
                k, v = line.split(':', 1)
                d[k.strip()] = v.strip()
        body = t[m.end():]
    else:
        body = t
    first = next((l.strip() for l in body.split('\n') if l.strip() and not l.startswith('#')), '')
    return d, first


def route_for(name, desc, mtype, routes):
    if mtype in ('project', 'reference'):
        return 'index_project_cold.md (if dormant; active projects go to MEMORY.md itself)'
    blob = (name + ' ' + desc).lower()
    best, score = None, 0
    for idx, kws in routes.items():
        s = sum(1 for k in kws if k.lower() in blob)
        if s > score:
            best, score = idx, s
    return best or 'index_feedback_ops.md (default)'


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='Draft index-line material for orphan memories')
    ap.add_argument('--memory-dir', default=None)
    ap.add_argument('--routes', default=None, help='JSON file: {sub-index: [keywords]}')
    ap.add_argument('--limit', type=int, default=0)
    a = ap.parse_args()

    routes = DEFAULT_ROUTES
    if a.routes:
        with open(a.routes, encoding='utf-8') as fh:
            routes = json.load(fh)

    z = load_zone(a.memory_dir or default_memory_dir())
    if z is None:
        print('memory dir not found')
        return 1
    orphans = sorted(z['bodies'] - z['linked'])
    if a.limit:
        orphans = orphans[:a.limit]

    print(f'{len(orphans)} orphans (body exists, no index line = never auto-recalled)')
    print('Write index lines as WHEN-to-open triggers, not summaries.\n')
    buckets = {}
    for name in orphans:
        d, first = meta(os.path.join(z['path'], name + '.md'))
        mtype = d.get('type', '')
        if not mtype:
            mtype = 'project' if name.startswith('project_') else (
                'reference' if name.startswith('reference_') else 'feedback')
        idx = route_for(name, d.get('description', '') + ' ' + first, mtype, routes)
        buckets.setdefault(idx, []).append((name, d.get('description', '') or first))

    for idx in sorted(buckets):
        print(f'## {idx}  ({len(buckets[idx])})')
        for name, desc in buckets[idx]:
            print(f'  - [{name}]({name}.md) - {desc[:96]}')
        print()
    print('Routing is a keyword match suggestion. The final decision is human/agent.')
    print('After adding index lines, rerun scan.py and confirm the orphan count went down.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
