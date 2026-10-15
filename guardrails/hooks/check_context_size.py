# -*- coding: utf-8 -*-
"""PostToolUse (Bash): watch the current session's context size and nudge toward /compact.

Why: a token-usage audit showed that requests writing more than 100k tokens of prompt
cache made up about 5% of requests but about 35% of total cost. Those are the moments
when the 5-minute prompt-cache TTL expired and the whole context was written again.
TTL expiry cannot be avoided (you cannot predict when the user steps away); what can be
controlled is the cost per expiry, which equals the context size at that moment.

Non-blocking: returns additionalContext only. The agent cannot run /compact itself,
so the message tells it to hand the user a ready-to-paste /compact line.
Each threshold fires once per session; dropping below 150k (after a compact) re-arms it.

  python check_context_size.py --selftest
  python check_context_size.py --reset
  python check_context_size.py --show      (uses CLAUDE_CODE_SESSION_ID)
"""
import glob
import json
import os
import sys
import time

HOME = os.path.expanduser('~')
PROJECTS = os.path.join(HOME, '.claude', 'projects')
STATE = os.path.join(HOME, '.claude', '.cache', 'context-size.json')

WINDOW_DEFAULT = 1_000_000
TAIL_BYTES = 262144      # read only the tail of the transcript; a full scan per Bash call is too slow
MAX_SESSIONS = 40

# This is a cost axis (absolute tokens re-written per cache miss), not "how much window
# is left". 300k tokens can be 30% of a 1M window and still be expensive per miss,
# so labels say "cost" to avoid confusion with remaining-window indicators.
LEVELS = [
    (200_000, 'cost notice', 'consider `/compact` at a natural break.'),
    (300_000, 'cost warning', '**`/compact` at the next break.** Each cache miss is expensive from here.'),
    (400_000, 'cost urgent', '**`/compact` now, or save a handoff and start a new session.**'),
]


def _load():
    try:
        with open(STATE, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, STATE)
    except Exception:
        pass


def find_jsonl(sid):
    if not sid:
        return None
    hits = glob.glob(os.path.join(PROJECTS, '*', sid + '.jsonl'))
    return hits[0] if hits else None


def tail_usage(path, tail_bytes=TAIL_BYTES):
    """Context size of the last entry with usage: input + cache_read + cache_creation."""
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            if size > tail_bytes:
                f.seek(size - tail_bytes)
                f.readline()
            raw = f.read()
    except Exception:
        return None
    for line in reversed(raw.split(b'\n')):
        if b'"usage"' not in line:
            continue
        try:
            u = (json.loads(line).get('message') or {}).get('usage') or {}
        except Exception:
            continue
        n = ((u.get('input_tokens') or 0)
             + (u.get('cache_read_input_tokens') or 0)
             + (u.get('cache_creation_input_tokens') or 0))
        if n:
            return n
    return None


def level_for(tokens):
    hit = None
    for thr, label, advice in LEVELS:
        if tokens >= thr:
            hit = (thr, label, advice)
    return hit


def check(payload):
    sid = payload.get('session_id') or os.environ.get('CLAUDE_CODE_SESSION_ID', '')
    path = find_jsonl(sid)
    if not path:
        return None
    tokens = tail_usage(path)
    if not tokens:
        return None
    window = int(os.environ.get('GUARD_CTX_WINDOW', WINDOW_DEFAULT))
    hit = level_for(tokens)

    st = _load()
    ent = st.get(sid) or {'fired': 0, 'peak': 0}
    prev = ent.get('fired', 0)
    ent['peak'] = max(ent.get('peak', 0), tokens)

    msg = None
    if hit:
        thr, label, advice = hit
        if thr > prev:
            pct = 100.0 * tokens / window
            msg = ('[%s] Context is %s tokens (%.1f%% of window). %s\n'
                   'At this size, every prompt-cache expiry re-writes this whole amount.\n'
                   'Tell the user (you cannot run /compact yourself):\n'
                   '  1. the current size and why now is a good break\n'
                   '  2. a paste-ready `/compact <topics to keep, 40-80 chars>` line\n'
                   '  3. if switching tasks, a handoff + new session is lighter than compact\n'
                   'This fires once per threshold (200k/300k/400k). Do not swallow it.'
                   % (label, format(tokens, ','), pct, advice))
            ent['fired'] = thr
    elif prev and tokens < 150_000:
        ent['fired'] = 0
    st[sid] = ent
    _prune(st)
    _save(st)
    return msg


def _prune(st):
    if len(st) <= MAX_SESSIONS:
        return
    for k in sorted(st, key=lambda k: (st[k] or {}).get('peak', 0))[:len(st) - MAX_SESSIONS]:
        st.pop(k, None)


def main():
    if '--reset' in sys.argv:
        try:
            os.remove(STATE)
            print('state reset: %s' % STATE)
        except FileNotFoundError:
            print('no state file')
        return
    if '--show' in sys.argv:
        sid = os.environ.get('CLAUDE_CODE_SESSION_ID', '')
        path = find_jsonl(sid)
        if not path:
            print('transcript not found (sid=%s)' % (sid[:8] or 'empty'))
            return
        tok = tail_usage(path)
        w = int(os.environ.get('GUARD_CTX_WINDOW', WINDOW_DEFAULT))
        hit = level_for(tok or 0)
        print('sid=%s / %s tok / %.1f%% of window / level=%s'
              % (sid[:8], format(tok or 0, ','), 100.0 * (tok or 0) / w, hit[1] if hit else 'none'))
        return
    if '--selftest' in sys.argv:
        sys.exit(0 if selftest() else 1)
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    msg = check(payload)
    if not msg:
        return
    print(json.dumps({'hookSpecificOutput': {
        'hookEventName': 'PostToolUse',
        'additionalContext': msg,
    }}, ensure_ascii=False))


def selftest():
    global STATE, PROJECTS
    import shutil
    import tempfile
    d = tempfile.mkdtemp(prefix='ctxsize_selftest_')
    STATE = os.path.join(d, 'state.json')
    PROJECTS = os.path.join(d, 'projects')
    proj = os.path.join(PROJECTS, 'testproj')
    os.makedirs(proj, exist_ok=True)
    ok = True

    def write_jsonl(sid, tokens):
        p = os.path.join(proj, sid + '.jsonl')
        with open(p, 'w', encoding='utf-8') as f:
            f.write(json.dumps({'type': 'user', 'x': 1}) + '\n')
            f.write(json.dumps({'message': {'usage': {
                'input_tokens': 10,
                'cache_read_input_tokens': tokens - 10,
                'cache_creation_input_tokens': 0}}}) + '\n')

    def P(sid):
        return {'session_id': sid, 'tool_input': {'command': 'ls'}}

    cases = [
        ('150k stays silent', 150_000, False),
        ('200k notice', 200_000, True),
        ('same level does not refire', 210_000, False),
        ('300k warning', 300_000, True),
        ('400k urgent', 400_000, True),
        ('same level does not refire (>400k)', 450_000, False),
    ]
    for label, tok, expect in cases:
        write_jsonl('S1', tok)
        got = check(P('S1')) is not None
        ok &= got == expect
        print('%s %-36s %9s tok -> fired=%s (expected %s)'
              % ('PASS' if got == expect else 'FAIL', label, format(tok, ','), got, expect))

    write_jsonl('S1', 120_000)
    check(P('S1'))
    write_jsonl('S1', 220_000)
    rearm = check(P('S1')) is not None
    ok &= rearm
    print('%s re-arms after compact (<150k) and fires again above 200k' % ('PASS' if rearm else 'FAIL'))

    silent = check(P('NOSUCH')) is None
    ok &= silent
    print('%s silent when transcript is missing' % ('PASS' if silent else 'FAIL'))
    shutil.rmtree(d, ignore_errors=True)
    print('\n%s' % ('ALL PASS' if ok else 'MISMATCH'))
    return ok


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        pass
