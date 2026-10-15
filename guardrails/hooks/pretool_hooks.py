# -*- coding: utf-8 -*-
"""Run every PreToolUse guard in ONE process.

Why: a hook's cost is dominated by how many processes it starts. Registering four
separate hooks on Write measured ~1.5 s on every Write. This dispatcher is registered once and runs the checks in-process.

Safety design (never weaken a guard to gain speed):
  - Check scripts are NOT rewritten for the dispatcher. Each is executed as `__main__`
    via runpy with stdin/stdout/stderr and SystemExit captured, so every script still
    works standalone and its verdict and output format are identical in both modes.
  - Verdicts are merged strongest-wins: deny > ask > allow. One deny is a deny.
  - Error handling is chosen per part (`on_error`):
      'open'   a crashing check is skipped and the crash is reported on stderr.
               Used where blocking on a bug would brick normal work (e.g. every Bash call).
      'closed' a crashing check becomes a deny. Used where the safe fallback is cheap
               (e.g. chat send: drafting still works).
    A crash in one part never removes another part's deny.
  - Which parts run is decided here by tool name, mirroring the settings matcher.

Self-test:  python pretool_hooks.py --selftest
"""
import io
import json
import os
import runpy
import sys
import time
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import guard_config as C  # noqa: E402

try:
    from hook_codecache import run_path_cached
except Exception:
    def run_path_cached(path):
        return runpy.run_path(path, run_name='__main__')

EDIT_TOOLS = {'Write', 'Edit', 'MultiEdit'}

# (name, script, tools, on_error)
PARTS = [
    ('instruction-edit', 'check_instruction_file_edit.py', EDIT_TOOLS, 'open'),
    ('mcp-deletion', 'check_mcp_deletion.py', EDIT_TOOLS, 'open'),
    ('memory-value-drop', 'check_memory_value_drop.py', EDIT_TOOLS, 'open'),
    ('bash-guard', 'bash_guard.py', {'Bash'}, 'open'),
    ('agent-fanout', 'check_agent_fanout.py', {'Agent', 'Task'}, 'open'),
    ('chat-send', 'check_chat_send_deny.py',
     set(C.CHAT_SEND_TOOLS) | set(C.CHAT_SCHEDULE_TOOLS), 'closed'),
    ('web-route', 'check_web_route.py', {'WebFetch', 'WebSearch'}, 'open'),
]


# Inline check: Write/Edit directly under PROTECTED_ROOT (e.g. ~/Documents/foo.md) is denied.
# Shell-based writes to the same place are an "ask" in bash_guard.py.
def root_direct_write(payload, tool):
    if tool not in ('Write', 'Edit', 'MultiEdit'):
        return None
    f = (payload.get('tool_input') or {}).get('file_path') or ''
    if not isinstance(f, str) or not f:
        return None
    p = os.path.normcase(os.path.abspath(os.path.expanduser(f)))
    root = os.path.normcase(os.path.abspath(str(C.PROTECTED_ROOT)))
    if not p.startswith(root + os.sep):
        return None
    first = p[len(root) + 1:].split(os.sep)[0]
    allowed = {os.path.normcase(a) for a in C.PROTECTED_ROOT_ALLOWED}
    if first in allowed:
        return None
    return {'permissionDecision': 'deny', 'permissionDecisionReason': (
        '[rule=root-direct-write] Do not write directly into %s. Use one of: %s '
        '(or the system temp dir for scratch files).'
        % (C.PROTECTED_ROOT.name, ', '.join(C.PROTECTED_ROOT_ALLOWED)))}


INLINE_CHECKS = (root_direct_write,)


def run_script(path, payload_json):
    """Run a hook script in-process. Returns (exit_code, stdout, stderr, exception_or_None)."""
    old = (sys.stdin, sys.stdout, sys.stderr, sys.argv)
    # BytesIO + TextIOWrapper (not StringIO) because some scripts touch `.buffer`.
    out_b, err_b = io.BytesIO(), io.BytesIO()
    out = io.TextIOWrapper(out_b, encoding='utf-8', errors='replace')
    err = io.TextIOWrapper(err_b, encoding='utf-8', errors='replace')
    sys.stdin = io.TextIOWrapper(io.BytesIO(payload_json.encode('utf-8')), encoding='utf-8')
    sys.stdout, sys.stderr = out, err
    sys.argv = [path]
    code, exc = 0, None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            run_path_cached(path)
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 0
    except Exception as e:  # noqa: BLE001
        exc = '%s raised %r' % (os.path.basename(path), e)
    finally:
        # flush, then detach: otherwise GC of the wrapper closes the BytesIO underneath
        for s in (sys.stdout, sys.stderr):
            try:
                s.flush()
                s.detach()
            except Exception:
                pass
        sys.stdin, sys.stdout, sys.stderr, sys.argv = old
    outtxt = out_b.getvalue().decode('utf-8', 'replace')
    errtxt = '\n'.join(l for l in err_b.getvalue().decode('utf-8', 'replace').splitlines()
                       if 'Warning:' not in l and not l.startswith('  '))
    return code, outtxt, errtxt, exc


RANK = {'deny': 3, 'ask': 2, 'allow': 1}


def collect(payload):
    tool = payload.get('tool_name') or ''
    payload_json = json.dumps(payload, ensure_ascii=False)
    decision = None            # (rank, dict)
    contexts, stderrs, times = [], [], {}
    exit2 = False

    def offer(d):
        nonlocal decision
        r = RANK.get(d.get('permissionDecision'), 0)
        if decision is None or r > decision[0]:
            decision = (r, d)

    for fn in INLINE_CHECKS:
        d = fn(payload, tool)
        if d:
            offer(d)

    for name, script, tools, on_error in PARTS:
        if tool not in tools:
            continue
        path = os.path.join(HERE, script)
        if not os.path.exists(path):
            stderrs.append('%s: script missing (%s)' % (name, script))
            if on_error == 'closed':
                offer({'permissionDecision': 'deny',
                       'permissionDecisionReason': '[rule=%s] guard script missing; failing closed' % name})
            continue
        t0 = time.time()
        code, out, err, exc = run_script(path, payload_json)
        times[name] = time.time() - t0
        if exc:
            stderrs.append(exc)
            if on_error == 'closed':
                offer({'permissionDecision': 'deny',
                       'permissionDecisionReason': '[rule=%s] guard crashed; failing closed: %s' % (name, exc)})
            continue
        if code == 2:
            exit2 = True
        if err.strip():
            stderrs.append(err.strip())
        if not out.strip():
            continue
        try:
            d = json.loads(out)
        except Exception:
            contexts.append(out.strip())
            continue
        hso = d.get('hookSpecificOutput') or {}
        pd = hso.get('permissionDecision')
        if pd:
            offer({'permissionDecision': pd,
                   'permissionDecisionReason': hso.get('permissionDecisionReason', '')})
        ctx = hso.get('additionalContext')
        if ctx:
            contexts.append(ctx)
    return {'decision': decision, 'contexts': contexts, 'stderr': stderrs,
            'exit2': exit2, 'times': times}


def main():
    if '--selftest' in sys.argv:
        sys.exit(0 if selftest() else 1)
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    r = collect(payload)
    hso = {'hookEventName': 'PreToolUse'}
    if r['decision']:
        hso.update(r['decision'][1])
    if r['contexts']:
        hso['additionalContext'] = '\n\n'.join(r['contexts'])
    if len(hso) > 1:
        print(json.dumps({'hookSpecificOutput': hso}, ensure_ascii=False))
    if r['stderr']:
        sys.stderr.write('\n'.join(r['stderr']))
    if r['exit2']:
        sys.exit(2)


def selftest():
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix='guard_selftest_')
    root = C.PROTECTED_ROOT
    try:
        # memory dir fixture: a note holding an amount
        memdir = os.path.join(tmp, 'memory')
        os.makedirs(memdir)
        note = os.path.join(memdir, 'project_demo.md')
        with open(note, 'w', encoding='utf-8') as f:
            f.write('# Demo deal\n- Amount: $12,000 (Stage 2), record DEAL-1042\n')
        C.MEMORY_DIRS = [memdir]   # checks import the same cached module object

        # MCP config fixture
        cfg = os.path.join(tmp, '.claude.json')
        with open(cfg, 'w', encoding='utf-8') as f:
            json.dump({'mcpServers': {'fetcher': {'command': 'x'}, 'chat': {'command': 'y'}}}, f)
        cfg_text = open(cfg, encoding='utf-8').read()

        bs = chr(92)
        cases = [
            ('Write directly under protected root', {
                'tool_name': 'Write', 'tool_input': {'file_path': str(root / 'foo.md'), 'content': 'x'}}, 'deny'),
            ('Write under allowed subdir', {
                'tool_name': 'Write', 'tool_input': {'file_path': str(root / '_output' / 'data' / 'a.csv'),
                                                     'content': 'x'}}, None),
            ('Edit protected instruction file', {
                'tool_name': 'Edit', 'tool_input': {'file_path': str(C.ASK_EDIT_FILES[0]),
                                                    'old_string': 'a', 'new_string': 'b'}}, 'ask'),
            ('Backslash in python -c', {
                'tool_name': 'Bash', 'tool_input': {'command': 'python -c "print(1)" # C:' + bs + 'x' + bs + '2026'}},
             'deny'),
            ('Backslash in heredoc body', {
                'tool_name': 'Bash', 'tool_input': {
                    'command': "python - <<'EOF'\np = 'C:" + bs + "data" + bs + "2026'\nEOF"}}, 'deny'),
            ('Backslash only in a normal arg', {
                'tool_name': 'Bash', 'tool_input': {'command': 'ls C:' + bs + 'tmp'}}, None),
            ('Plain Bash', {'tool_name': 'Bash', 'tool_input': {'command': 'ls -la'}}, None),
            ('cd + relative grep', {
                'tool_name': 'Bash', 'tool_input': {'command': 'cd ~/proj && grep -n TODO main.py'}}, 'deny'),
            ('grep with absolute path', {
                'tool_name': 'Bash', 'tool_input': {'command': 'grep -n "a|b" ~/proj/main.py | head'}}, None),
            ('CRM deal set to Closed Lost', {
                'tool_name': 'Bash', 'tool_input': {
                    'command': 'crm update deal --id DEAL-1042 --values "StageName=\'Closed Lost\'"'}}, 'deny'),
            ('CRM deal stage advance', {
                'tool_name': 'Bash', 'tool_input': {
                    'command': 'crm update deal --id DEAL-1042 --values "StageName=\'03 - Proposal\'"'}}, None),
            ('Direct API call', {
                'tool_name': 'Bash', 'tool_input': {'command': 'curl https://api.example.com/v1/items'}}, 'deny'),
            ('Direct API call, helper in another segment', {
                'tool_name': 'Bash', 'tool_input': {
                    'command': 'echo api_client.py; curl https://api.example.com/v1/items'}}, 'deny'),
            ('Direct API call, helper only in a comment', {
                'tool_name': 'Bash', 'tool_input': {
                    'command': 'curl https://api.example.com/v1/items # api_client.py'}}, 'deny'),
            ('API via helper', {
                'tool_name': 'Bash', 'tool_input': {
                    'command': 'python api_client.py get https://api.example.com/v1/items'}}, None),
            ('grep for the API host (search only)', {
                'tool_name': 'Bash', 'tool_input': {
                    'command': 'grep -rn "https://api.example.com/" ~/proj'}}, None),
            ('Public post mutation', {
                'tool_name': 'Bash', 'tool_input': {'command': 'node bot.js CreateTweet "hi"'}}, 'deny'),
            ('Public post as draft', {
                'tool_name': 'Bash', 'tool_input': {'command': 'node post-cli.js draft "CreateTweet is mentioned"'}},
             None),
            ('Agent spawn (note only)', {
                'tool_name': 'Agent', 'session_id': 'selftest-%d' % os.getpid(),
                'tool_input': {'subagent_type': 'web-researcher', 'prompt': 'x'}}, None),
            ('Chat immediate send', {
                'tool_name': C.CHAT_SEND_TOOLS[0], 'tool_input': {'channel_id': 'C1', 'message': 'x'}}, 'deny'),
            ('Chat scheduled send (note only)', {
                'tool_name': C.CHAT_SCHEDULE_TOOLS[0], 'tool_input': {'channel_id': 'C1', 'message': 'x'}}, None),
            ('Chat draft', {
                'tool_name': C.CHAT_DRAFT_TOOL, 'tool_input': {'channel_id': 'C1', 'message': 'x'}}, None),
            ('WebFetch search engine (warn only)', {
                'tool_name': 'WebFetch', 'tool_input': {'url': 'https://duckduckgo.com/?q=x'}}, None),
            ('Memory edit drops an amount', {
                'tool_name': 'Edit', 'tool_input': {'file_path': note, 'old_string': '$12,000',
                                                    'new_string': '$9,000'}}, 'deny'),
            ('Memory edit keeps old amount with marker', {
                'tool_name': 'Edit', 'tool_input': {
                    'file_path': note, 'old_string': '$12,000',
                    'new_string': '$9,000 [INVALIDATED 2026-10-15] old $12,000 -> new $9,000'}}, None),
            ('Memory edit unrelated text', {
                'tool_name': 'Edit', 'tool_input': {'file_path': note, 'old_string': '# Demo deal',
                                                    'new_string': '# Demo deal (renewal)'}}, None),
            ('Config edit drops an MCP server', {
                'tool_name': 'Write', 'tool_input': {
                    'file_path': cfg, 'content': json.dumps({'mcpServers': {'fetcher': {'command': 'x'}}})}}, 'deny'),
            ('Config edit breaks JSON', {
                'tool_name': 'Edit', 'tool_input': {'file_path': cfg, 'old_string': cfg_text[-1:],
                                                    'new_string': ''}}, 'deny'),
            ('Config edit adds an MCP server', {
                'tool_name': 'Write', 'tool_input': {
                    'file_path': cfg, 'content': json.dumps({'mcpServers': {
                        'fetcher': {'command': 'x'}, 'chat': {'command': 'y'}, 'new': {'command': 'z'}}})}}, None),
            ('Empty payload', {}, None),
            ('Garbage tool_input', {'tool_name': 'Bash', 'tool_input': 'not a dict'}, None),
        ]

        # WebSearch: deny only when the provider has no WebSearch
        os.environ.pop('GUARD_NO_WEBSEARCH', None)
        cases.append(('WebSearch, provider supports it (note only)', {
            'tool_name': 'WebSearch', 'tool_input': {'query': 'x'}}, None))

        ok = True
        print('%-46s %-6s %-6s %s' % ('case', 'expect', 'got', 'time'))
        for label, payload, expect in cases:
            t0 = time.time()
            try:
                r = collect(payload)
                got = r['decision'][1]['permissionDecision'] if r['decision'] else None
            except Exception as e:  # noqa: BLE001
                got = 'EXC:%r' % e
            dt = time.time() - t0
            mark = 'PASS' if got == expect else 'FAIL'
            ok &= got == expect
            print('%-46s %-6s %-6s %.3fs %s' % (label, expect, got, dt, mark))

        os.environ['GUARD_NO_WEBSEARCH'] = '1'
        r = collect({'tool_name': 'WebSearch', 'tool_input': {'query': 'x'}})
        got = r['decision'][1]['permissionDecision'] if r['decision'] else None
        os.environ.pop('GUARD_NO_WEBSEARCH', None)
        ok &= got == 'deny'
        print('%-46s %-6s %-6s %s' % ('WebSearch, provider lacks it', 'deny', got,
                                       'PASS' if got == 'deny' else 'FAIL'))

        # fail-closed: a crashing chat-send part must turn into deny
        global PARTS
        saved = PARTS
        broken = os.path.join(tmp, 'broken.py')
        with open(broken, 'w', encoding='utf-8') as f:
            f.write('raise RuntimeError("boom")\n')
        PARTS = [('chat-send', broken, set(C.CHAT_SEND_TOOLS), 'closed'),
                 ('bash-guard', broken, {'Bash'}, 'open')]
        r1 = collect({'tool_name': C.CHAT_SEND_TOOLS[0], 'tool_input': {}})
        r2 = collect({'tool_name': 'Bash', 'tool_input': {'command': 'ls'}})
        PARTS = saved
        g1 = r1['decision'][1]['permissionDecision'] if r1['decision'] else None
        g2 = r2['decision'][1]['permissionDecision'] if r2['decision'] else None
        ok &= g1 == 'deny' and g2 is None
        print('%-46s %-6s %-6s %s' % ('crash in fail-closed part', 'deny', g1, 'PASS' if g1 == 'deny' else 'FAIL'))
        print('%-46s %-6s %-6s %s' % ('crash in fail-open part', None, g2, 'PASS' if g2 is None else 'FAIL'))

        print('\n%s' % ('ALL PASS' if ok else 'MISMATCH - do not deploy'))
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        pass
