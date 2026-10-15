# -*- coding: utf-8 -*-
"""Measure the fixed per-turn cost of a Claude Code setup: hook run time and always-on
injected text, and warn when it grows.

    python hook-perf-audit.py              # measure (median of 3), table, verdict, diff vs last run
    python hook-perf-audit.py --quick      # 1 run per hook (fast, NOT for judging)
    python hook-perf-audit.py --list       # parse and list hooks without executing them
    python hook-perf-audit.py --json       # machine-readable
    python hook-perf-audit.py --no-save    # do not update the state file

Why: a hook's cost is dominated by **how many processes it starts**, not by what it does,
so several checks belong in one process. Hooks only ever get
added, so the point is to notice the moment they grow.

What it checks:
  1. measured seconds per hook and estimated process count (**async reported separately**)
  2. matcher breadth (a PreToolUse hook on `*` also runs for Read / Grep / Glob)
  3. duplicate registration: an old standalone hook in settings.local.json running next to a
     dispatcher in settings.json that already includes it
  4. always-on injected text (CLAUDE.md + rules files, number of <important if=...> blocks)
  5. diff vs the previous run (never compare absolute values across days)

Three corrections that change every conclusion if missed:
  1. **Do not measure through `shell=True`.** Through cmd.exe, `$HOME` is not expanded, so you
     measure "file not found, exit immediately" and **underestimate cost 3-10x**. This script
     calls bash directly for that reason.
  2. **Do not add `async: true` hooks to the blocking total.** They do not block the reply.
     They still cost CPU, so they are reported in a separate bucket with their own budget.
  3. **Process start cost is not one number.** Measured ranges on one machine:
     cmd 0.5-0.8s / bash 0.6-1.5s / python 0.8-1.6s / jq 0.5-2.1s / pwsh 2.7-3.0s.
     Values swing about +/-40% with the number of concurrent sessions, so judge improvements
     by before/after on the same machine, not by absolute numbers.

Hooks that are exact duplicates are NOT waste: Claude Code deduplicates identical hook commands.
Only *different* commands registered in settings.local.json that a dispatcher already covers
are reported.
"""
import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()

BUDGET = {                      # seconds per firing
    'UserPromptSubmit': 1.0,    # paid on every prompt
    'PreToolUse': 0.8,          # paid on every tool call
    'PostToolUse': 0.8,
    'Stop': 2.0,                # once per reply
    'SessionStart': 5.0,
}
INJECT_SOFT_KB = 40
IMPORTANT_IF_MAX = 12
DELTA_WARN = 0.30               # warn on +30% vs last run (change, not absolute)
# Async hooks leave the latency path but still pay CPU on every start. Not summed, not ignored.
# Override with HOOK_ASYNC_BUDGET to check that the warning actually fires
# ("silent" and "broken" look the same from outside).
ASYNC_BUDGET = float(os.environ.get('HOOK_ASYNC_BUDGET', '3.0'))
BROAD_MATCHERS = {'*', '', None, '.*'}
CHEAP_TOOLS_HINT = 'Read / Grep / Glob'

SAMPLE_PAYLOAD = {
    'prompt': 'Summarize the open items for the ACME City project',
    'cwd': str(Path.cwd()),
    'workspace': {'current_dir': str(Path.cwd())},
    'tool_name': 'Bash',
    'tool_input': {'command': 'echo hello'},
}


def find_bash(explicit):
    if explicit:
        return explicit
    for cand in (r'C:\Program Files\Git\bin\bash.exe', '/bin/bash', '/usr/bin/bash'):
        if os.path.exists(cand):
            return cand
    return shutil.which('bash')


def procs_in(cmd):
    """Rough number of processes a command line starts."""
    return 1 + cmd.count('|') + len(re.findall(r'\$\(', cmd)) + cmd.count('&&')


def measure(cmd, bash, n=3):
    """Never shell=True: through cmd.exe `$HOME` is not expanded and cost is underestimated 3-10x."""
    ts = []
    payload = json.dumps(SAMPLE_PAYLOAD, ensure_ascii=False)
    runner = [bash, '-c', cmd] if bash else cmd
    for _ in range(n):
        t0 = time.time()
        try:
            subprocess.run(runner, shell=not isinstance(runner, list), input=payload,
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=60)
        except Exception:
            return None
        ts.append(time.time() - t0)
    return statistics.median(ts)


def script_name(cmd):
    m = re.findall(r'([A-Za-z0-9_\-]+\.(?:py|sh|ps1|js))', cmd)
    return m[-1] if m else None


def local_duplicates(user_settings, local_path, scripts_dir):
    """Standalone hooks in settings.local.json that a dispatcher in settings.json already runs.

    Exact duplicates are skipped (deduplicated by Claude Code). A dispatcher is any script
    named *-hooks.py; its source is read to confirm it really names the standalone script.
    A blanket "dispatcher exists, so every local hook is a duplicate" rule produced a false
    positive on an independent guard.
    """
    if not local_path or not os.path.exists(local_path):
        return []
    try:
        with open(local_path, encoding='utf-8') as fh:
            local = json.load(fh)
    except Exception:
        return []
    out = []
    for event, entries in (local.get('hooks') or {}).items():
        user_scripts = set()
        for e in (user_settings.get('hooks') or {}).get(event, []):
            for h in e.get('hooks', []):
                s = script_name(h.get('command', ''))
                if s:
                    user_scripts.add(s)
        dispatcher_src = ''
        for s in user_scripts:
            if s.endswith('-hooks.py'):
                try:
                    with open(os.path.join(scripts_dir, s), encoding='utf-8', errors='replace') as fh:
                        dispatcher_src += fh.read()
                except OSError:
                    pass
        for e in entries:
            for h in e.get('hooks', []):
                s = script_name(h.get('command', ''))
                if not s or s in user_scripts:
                    continue
                if dispatcher_src and (s in dispatcher_src or s[:-3] in dispatcher_src):
                    out.append((event, e.get('matcher'), s))
    return out


def audit(args):
    n = 1 if args.quick else 3
    try:
        with open(args.settings, encoding='utf-8') as fh:
            settings = json.load(fh)
    except Exception as e:
        return {'error': 'cannot read settings file: %r' % e}
    bash = find_bash(args.bash)

    result = {'events': {}, 'inject': {}, 'warnings': [], 'notes': []}
    for event, entries in (settings.get('hooks') or {}).items():
        rows = []
        for e in entries:
            for h in e.get('hooks', []):
                cmd = h.get('command', '')
                if not cmd:
                    continue
                rows.append({'matcher': e.get('matcher'), 'cmd': cmd,
                             'async': bool(h.get('async')), 'procs': procs_in(cmd),
                             'sec': None if args.list else measure(cmd, bash, n)})
        # Aggregate per matcher: one Write only runs the matcher=Write hooks, so summing the
        # whole event counts cost that is never paid. Async is excluded from the sum.
        by_matcher, async_sec = {}, 0.0
        for r in rows:
            if r['async']:
                async_sec += r['sec'] or 0
                continue
            d = by_matcher.setdefault(r['matcher'] or '(all)', {'sec': 0.0, 'procs': 0, 'n': 0})
            d['sec'] += r['sec'] or 0
            d['procs'] += r['procs']
            d['n'] += 1
        if not by_matcher:
            by_matcher = {'(async only)': {'sec': 0.0, 'procs': 0, 'n': 0}}
        worst = max(by_matcher.items(), key=lambda kv: kv[1]['sec'])
        result['events'][event] = {'hooks': rows, 'by_matcher': by_matcher, 'worst': worst[0],
                                   'total_sec': worst[1]['sec'], 'async_sec': async_sec,
                                   'procs': worst[1]['procs']}
        budget = BUDGET.get(event)
        if budget and worst[1]['sec'] > budget:
            result['warnings'].append(
                '[RED] %s[matcher=%s] takes %.2fs per firing (budget %.1fs, %d hooks / %d processes)'
                ' -> fold into one process or make it async; do not just delete hooks'
                % (event, worst[0], worst[1]['sec'], budget, worst[1]['n'], worst[1]['procs']))
        if async_sec > ASYNC_BUDGET:
            result['warnings'].append(
                '[YELLOW] %s async hooks take %.2fs (budget %.1fs). They do not block, but CPU is'
                ' paid on every start. Fix in order: fold -> reduce frequency -> remove'
                ' (measured example: SessionStart 9.73s -> 0.56s)' % (event, async_sec, ASYNC_BUDGET))
        if event in ('PreToolUse', 'PostToolUse'):
            for m, mm in by_matcher.items():
                if (m in BROAD_MATCHERS or m == '(all)') and mm['n']:
                    result['warnings'].append(
                        '[YELLOW] %s has %d hook(s) with matcher=%s (all tools), so they also run'
                        ' for %s. List the target tools explicitly'
                        ' (measured example: Read went from 4.8s to 0.0s)' % (event, mm['n'], m, CHEAP_TOOLS_HINT))
        for r in rows:
            if 'jq ' in r['cmd'] and r['cmd'].strip().startswith(('bash', 'sh')):
                result['warnings'].append(
                    '[YELLOW] %s: a bash+jq hook folded into one Python process saves about %.1fs'
                    % (event, max(0.0, (r['sec'] or 0) - 0.3)))
            if ('pwsh' in r['cmd'] or 'powershell' in r['cmd'].lower()) and not r['async']:
                result['warnings'].append(
                    '[YELLOW] %s: a blocking pwsh hook costs about 3x python (2.7-3.0s).'
                    ' If it only notifies, set "async": true' % event)

    dups = local_duplicates(settings, args.local_settings, args.scripts_dir)
    if dups:
        result['warnings'].append(
            '[RED] settings.local.json has %d standalone hook(s) that a dispatcher already runs'
            ' (measured example: 19 found; duplicates inject the always-on rules twice per turn)' % len(dups))
        for ev, m, s in dups[:12]:
            result['notes'].append('     local: %s[%s] %s' % (ev, m, s))
    result['local_duplicates'] = [{'event': e, 'matcher': m, 'script': s} for e, m, s in dups]

    inj = 0
    paths = [args.claude_md] + list(args.extra_inject or [])
    if args.rules_dir and os.path.isdir(args.rules_dir):
        paths += [os.path.join(args.rules_dir, f) for f in sorted(os.listdir(args.rules_dir))]
    for p in paths:
        try:
            b = os.path.getsize(p)
        except (OSError, TypeError):
            continue
        inj += b
        result['inject'][os.path.basename(p)] = b
    result['inject']['_total_bytes'] = inj
    try:
        with open(args.claude_md, encoding='utf-8') as fh:
            n_if = len(re.findall(r'<important if=', fh.read()))
    except (OSError, TypeError):
        n_if = 0
    result['inject']['_important_if'] = n_if
    if inj > INJECT_SOFT_KB * 1024:
        result['warnings'].append('[YELLOW] always-on injection %.1fKB (target %dKB). Move parts of'
                                  ' CLAUDE.md or rules into on-demand reference files'
                                  % (inj / 1024, INJECT_SOFT_KB))
    if n_if > IMPORTANT_IF_MAX:
        result['warnings'].append('[YELLOW] %d <important if> blocks (target %d). "if" is not a'
                                  ' branch: the full text is injected every time' % (n_if, IMPORTANT_IF_MAX))
    return result


def load_state(path):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return None


def save_state(r, path):
    snap = {'at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'events': {e: {'worst': d['worst'], 'sec': d['total_sec']} for e, d in r['events'].items()},
            'inject_bytes': r['inject'].get('_total_bytes'),
            'important_if': r['inject'].get('_important_if')}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(snap, fh, ensure_ascii=False, indent=2)


def print_delta(r, prev, quick=False):
    """Before/after on the same machine only. A --quick diff is reference only:
    single runs swing +/-40% and flag red every time."""
    print('\n== Diff vs previous run (%s)' % prev.get('at', '?'))
    print('  --quick is a single run; treat the diff as reference only' if quick
          else '  Process start cost varies with concurrent sessions; read the rate of change')
    for e, d in sorted(r['events'].items()):
        old = (prev.get('events') or {}).get(e)
        if not old:
            print('  + %s - new' % e)
            continue
        o, cur = old.get('sec') or 0, d['total_sec']
        if o <= 0:
            continue
        rate = (cur - o) / o
        mark = '  ' if quick else ('UP' if rate > DELTA_WARN else ('DN' if rate < -DELTA_WARN else '  '))
        print('  %s %-18s %.2fs -> %.2fs (%+.0f%%)' % (mark, e, o, cur, rate * 100))
    ob, nb = prev.get('inject_bytes') or 0, r['inject'].get('_total_bytes') or 0
    if ob:
        print('  %s always-on injection %.1fKB -> %.1fKB (%+d B)'
              % ('UP' if nb > ob else '  ', ob / 1024, nb / 1024, nb - ob))


def short_name(cmd):
    """Script file name if the command runs one, else the command itself (truncated)."""
    return (script_name(cmd) or cmd.strip())[:44]


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser(description='Measure Claude Code hook cost and always-on injection size.')
    ap.add_argument('--settings', default=str(HOME / '.claude' / 'settings.json'))
    ap.add_argument('--local-settings', default=str(Path.cwd() / '.claude' / 'settings.local.json'))
    ap.add_argument('--scripts-dir', default=str(HOME / '.claude' / 'scripts'),
                    help='where dispatcher scripts (*-hooks.py) live')
    ap.add_argument('--claude-md', default=str(Path.cwd() / 'CLAUDE.md'))
    ap.add_argument('--rules-dir', default=str(Path.cwd() / '.claude' / 'rules'))
    ap.add_argument('--extra-inject', action='append', help='other always-on file to count (repeatable)')
    ap.add_argument('--state', default=str(HOME / '.claude' / 'hook_perf_state.json'))
    ap.add_argument('--bash', default=None, help='bash executable (auto-detected)')
    ap.add_argument('--quick', action='store_true', help='1 run per hook instead of median of 3')
    ap.add_argument('--list', action='store_true', help='list hooks without executing them')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--no-save', action='store_true')
    args = ap.parse_args()

    r = audit(args)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    if 'error' in r:
        print(r['error'])
        return 1

    print('== Hooks (per firing, aggregated per matcher, async separate)%s'
          % ('  [--list: not executed]' if args.list else ''))
    for event, d in sorted(r['events'].items()):
        extra = ('  [+async %.2fs, non-blocking]' % d['async_sec']) if d['async_sec'] else ''
        print('  %s - worst matcher=%s %.2fs / %d hooks / %d processes%s'
              % (event, d['worst'], d['total_sec'], d['by_matcher'][d['worst']]['n'], d['procs'], extra))
        for m, mm in sorted(d['by_matcher'].items(), key=lambda kv: -kv[1]['sec']):
            print('      [%-12s] %.2fs (%d)' % (m, mm['sec'], mm['n']))
            for h in d['hooks']:
                if h['async'] or (h['matcher'] or '(all)') != m:
                    continue
                sec = '%.2fs' % h['sec'] if h['sec'] is not None else '  -  '
                print('           %6s  %s' % (sec, short_name(h['cmd'])))
        for h in d['hooks']:
            if h['async']:
                sec = '%.2fs' % h['sec'] if h['sec'] is not None else '  -  '
                print('      [async       ] %6s  %s' % (sec, short_name(h['cmd'])))
    inj = r['inject']
    print('\n== Always-on injection %.1fKB / %d important-if blocks'
          % (inj['_total_bytes'] / 1024, inj['_important_if']))

    if not args.list:
        prev = load_state(args.state)
        if prev:
            print_delta(r, prev, quick=args.quick)

    print('\n== Verdict')
    if r['warnings']:
        for w in r['warnings']:
            print('  ' + w)
        for note in r['notes']:
            print(note)
    else:
        print('  OK: within budget')
    print('\n  Note: exact duplicate hooks are deduplicated by Claude Code. They are not waste; do not delete them.')

    if not args.no_save and not args.list:
        save_state(r, args.state)
    return 0


if __name__ == '__main__':
    sys.exit(main())
