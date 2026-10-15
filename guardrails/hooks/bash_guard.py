#!/usr/bin/env python3
"""bash_guard.py - PreToolUse guard for the Bash tool (several checks, one process).

Why one process: a hook's cost is dominated by how many processes it starts.
As separate scripts, one Bash call launched about 20 processes for its hooks
(jq pipelines, shell scripts, two Python checks), roughly 5 seconds per Bash call. This file performs the same checks in a single interpreter.

Checks (run in this order, first deny/ask wins):
  1. irreversible business actions   deny  (configurable, see guard_config.py)
  2. per-segment deny/allow rules     deny  (configurable, see guard_config.py)
  3. writes to protected instruction files via shell      ask
  4. writes directly into the protected output root       ask
  5. backslashes inside heredoc bodies / `python -c`       deny
  6. `cd somewhere && grep relative.file`                  deny

Output: on deny/ask, a single hookSpecificOutput JSON on stdout and exit 0.
Every reason starts with `[rule=...]` so regression reports can group by rule.
"""

import json
import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard_config as C  # noqa: E402


def grep_e(pattern, text):
    """Same meaning as `echo "$cmd" | grep -qE pattern` (line by line).

    Do not turn this into a whole-string re.search: a class like `[^|;&]` matches
    newlines in Python, so greedy patterns would span lines and over-deny
    multi-line commands. A corpus regression caught 5 false blocks exactly like that.
    """
    return any(re.search(pattern, line) for line in text.splitlines())


# Allowlists ("exempt if X is present") must be evaluated per command segment, never
# on the whole line. Bypasses a whole-line allowlist would let through:
#   echo helper.py; curl https://api...          -> direct call passes
#   some-write-command ... # helper-name          -> a COMMENT exempts it
# Deny-side patterns are robust to compound commands; only the allow side is dangerous.
# Newlines are deliberately NOT separators: heredoc bodies and multi-line python are
# not separate commands, and splitting on them produced false blocks in the corpus.
_SEG_SPLIT = re.compile(r'(?:\|\||&&|[;|])')
_TRAILING_COMMENT = re.compile(r'(?<!\S)#.*$')
_SEARCH_ONLY_RE = re.compile(
    r'^\s*(?:sudo\s+)?(?:grep|rg|egrep|fgrep|echo|printf|cat|ls|find|wc|sed|awk|head|tail)\b')


def segments(cmd):
    return [s for s in _SEG_SPLIT.split(cmd) if s.strip()]


def strip_comment(seg):
    return _TRAILING_COMMENT.sub('', seg)


def violating_segments(deny_pat, allow_pat, cmd, skip_search_only=False):
    bad = []
    for seg in segments(cmd):
        if not re.search(deny_pat, seg):
            continue
        body = strip_comment(seg)
        if skip_search_only and _SEARCH_ONLY_RE.search(body):
            continue
        if allow_pat and re.search(allow_pat, body):
            continue
        bad.append(seg)
    return bad


def _emit(decision, rule, reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": "[rule=%s] %s" % (rule, reason),
        }
    }, ensure_ascii=False))
    sys.exit(0)


def deny(rule, reason):
    _emit("deny", rule, reason)


def ask(rule, reason):
    """Unlike deny, a human can pass this with one click (keeps a legitimate path open)."""
    _emit("ask", rule, reason)


# ---------------------------------------------------------------- 1
def check_irreversible(cmd):
    for r in C.IRREVERSIBLE_ACTION_RULES:
        for pat in r['patterns']:
            if re.search(pat, cmd, re.IGNORECASE):
                deny(r['rule'], r['reason'])


# ---------------------------------------------------------------- 2
def check_segment_rules(cmd):
    for r in C.SEGMENT_RULES:
        if violating_segments(r['deny'], r.get('allow'), cmd,
                              skip_search_only=r.get('skip_search_only', False)):
            deny(r['rule'], r['reason'])


# ---------------------------------------------------------------- 3 (ask)
# Whole-string on purpose: the common path is multi-line python where the path
# literal and open(P, 'w') sit on different lines. It is only "ask", so an
# over-match costs one click.
def _instruction_names():
    return sorted({p.name for p in C.ASK_EDIT_FILES})


def check_instruction_write(cmd):
    for name in _instruction_names():
        n = re.escape(name)
        if not re.search(n, cmd):
            continue
        writes = [
            re.compile(r">>?\s*[\"']?[^\s|;&]*" + n),
            re.compile(r"sed\s+(-[a-zA-Z]+\s+)*-i[^|;&]*" + n),
            re.compile(r"tee\s+[^|;&]*" + n),
            re.compile(r"open\([^)]*['\"]w['\"]"),
        ]
        if any(p.search(cmd) for p in writes):
            ask('instruction-file-write',
                "Shell write to %s detected. Shared rules live in %s; edit that instead. "
                "If this is an agent-specific section, approve and continue."
                % (name, C.CANONICAL_RULES_FILE))


# ---------------------------------------------------------------- 4 (ask)
# The Write/Edit tools are covered by the dispatcher's inline check; shell writes are
# the gap. Some agents' native patch tools do not fire PreToolUse at all, so this is
# the only layer that sees their shell-based writes.
# Ask (not deny) because an allowlist miss should cost one click, not a blocked task.
_DOCS_WRITE_RES = [
    re.compile(r">>?\s*[\"']?[^\s|;&]*%s[/\\]"),
    re.compile(r"(Set-Content|Out-File|Add-Content|New-Item)\b", re.I),
    re.compile(r"\btee\s"),
    re.compile(r"(Copy-Item|Move-Item)\b", re.I),
    re.compile(r"\b(cp|mv)\s"),
    re.compile(r"open\([^)]*['\"][wa]['\"]"),
]


def check_root_direct_write(cmd):
    root = C.PROTECTED_ROOT.name
    if not root or root not in cmd:
        return
    # `[/\\]{1,2}` because paths inside python strings often carry doubled backslashes
    path_re = re.compile(re.escape(root) + r"[/\\]{1,2}([^\s/\\'\"|;&()]+)")
    hits = [m.group(1) for m in path_re.finditer(cmd)]
    stray = [h for h in hits
             if not any(h.startswith(a) for a in C.PROTECTED_ROOT_ALLOWED) and '.' in h]
    if not stray:
        return
    writes = [re.compile(p.pattern % re.escape(root)) if '%s' in p.pattern else p
              for p in _DOCS_WRITE_RES]
    if any(p.search(cmd) for p in writes):
        ask('root-direct-write',
            "Write directly into %s detected (%s). Put artifacts under one of: %s; "
            "temp files under the system temp dir. If the target is allowed, approve and continue."
            % (root, ', '.join(sorted(set(stray))[:3]), ', '.join(C.PROTECTED_ROOT_ALLOWED)))


# ---------------------------------------------------------------- 5
# A backslash inside inline python or a heredoc is halved before python sees it.
# `'\\'` -> `'\'` is a SyntaxError (visible), but `'C:\dir\2026'` turns `\2` into an
# octal escape and is persisted corrupted, silently. A rule in the prompt depends on
# the model remembering it; this hook does not.
# There is always a strictly better path: write a script file with the Write tool
# (no shell involved) and run it. No env-var escape hatch on purpose.
HEREDOC_START_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
PY_INLINE_RE = re.compile(r"\bpython[0-9.]*\s+(?:-[^\s-]\S*\s+)*-c\b")


def _heredoc_bodies(cmd):
    """Only heredoc bodies; scanning the whole command would hit Windows path args."""
    lines = cmd.splitlines()
    bodies, i = [], 0
    while i < len(lines):
        m = HEREDOC_START_RE.search(lines[i])
        if not m:
            i += 1
            continue
        delim, body = m.group(2), []
        i += 1
        while i < len(lines) and lines[i].strip() != delim:
            body.append(lines[i])
            i += 1
        bodies.append('\n'.join(body))
        i += 1
    return bodies


def check_inline_backslash(cmd):
    if '\\' not in cmd:
        return
    hits = [b for b in _heredoc_bodies(cmd) if '\\' in b]
    kind = 'heredoc body'
    if not hits and PY_INLINE_RE.search(cmd):
        hits, kind = [cmd], 'python -c argument'
    if not hits:
        return
    deny('inline-backslash',
         "A backslash is present in the %s. The shell halves backslashes before python "
         "receives them, and sequences like `\\2` silently become octal escapes and are "
         "persisted corrupted.\n"
         "Do this instead:\n"
         "  1. Write the code to a script file with the Write tool (no shell involved), "
         "e.g. %sYYYYMMDD_task.py\n"
         "  2. Run it with python.\n"
         "If you do not need the backslash, remove it (python accepts `/` on Windows)."
         % (kind, C.SCRATCH_DIR_HINT))


# ---------------------------------------------------------------- 6
# `cd {dir} && grep {relative.file}` is not dangerous; it costs the human a click.
# The harness cannot statically resolve paths after `cd`, so when Read() deny rules
# exist it cannot prove the target is allowed and asks the user, even in bypass mode.
# Absolute paths, or the Grep/Read tools, resolve statically and never reach a human.
_READER_RE = re.compile(
    r'^\s*(?:sudo\s+)?(grep|rg|egrep|fgrep|cat|head|tail|less|wc|nl|sed|awk)\b')
_PATTERN_FIRST = {'grep', 'rg', 'egrep', 'fgrep', 'sed', 'awk'}
# Flags taking a separate value token. Never add `-n` (grep -n takes no value; adding it
# made the pattern be eaten as a value and the file name slip through).
_VALUE_FLAGS = {'-e', '-f', '--include', '--exclude', '--glob', '-g', '--type',
                '-t', '-m', '-A', '-B', '-C', '--max-count'}


def _looks_relative_file(tok):
    if not tok or tok.startswith('-'):
        return False
    if '/' in tok or '\\' in tok:
        return False
    if tok.startswith('$') or tok.startswith('`'):
        return False
    return tok == '.' or re.search(r'\.[A-Za-z0-9]{1,8}$', tok) is not None


def _strip_heredoc_bodies(cmd):
    lines, out, i = cmd.splitlines(), [], 0
    while i < len(lines):
        out.append(lines[i])
        m = HEREDOC_START_RE.search(lines[i])
        i += 1
        if m:
            while i < len(lines) and lines[i].strip() != m.group(2):
                i += 1
            i += 1
    return '\n'.join(out)


def check_cd_relative_read(cmd):
    if not re.search(r'(?:^|[;&|]|\s)(?:cd|pushd)\s', cmd):
        return
    cmd = _strip_heredoc_bodies(cmd)
    orig = cmd
    # Newlines are command separators here; shlex would treat them as spaces.
    cmd = cmd.replace('\\\n', ' ').replace('\n', ' ;\n')
    # Use shlex (quote-aware), not segments(): `grep "a|b" f.md` must not split at the quoted |.
    # punctuation_chars peels `;` off words like `head -5;`.
    try:
        lx = shlex.shlex(cmd, posix=True, punctuation_chars=';&|')
        lx.whitespace_split = True
        lx.commenters = '#'
        all_toks = list(lx)
    except ValueError:
        return

    commands, cur = [], []
    for t in all_toks:
        if t and set(t) <= set(';&|'):
            if cur:
                commands.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        commands.append(cur)

    for toks in commands:
        toks = [t for t in toks if t != 'sudo']
        if not toks:
            continue
        m = _READER_RE.search(toks[0])
        if not m:
            continue
        prog = m.group(1)
        toks = toks[1:]
        # sed -i / awk -i inplace are edits, not reads; Grep/Read cannot replace them.
        if prog in ('sed', 'awk') and any(t == '-i' or t.startswith('-i') for t in toks):
            continue
        args, skip_next = [], False
        for t in toks:
            if skip_next:
                skip_next = False
                continue
            if t in _VALUE_FLAGS:
                skip_next = True
                continue
            if t.startswith('-'):
                continue
            args.append(t)
        if prog in _PATTERN_FIRST and args:
            args = args[1:]
        # posix shlex eats unquoted backslashes; ignore tokens not verbatim in the original.
        bad = [a for a in args if _looks_relative_file(a) and a in orig]
        if not bad:
            continue
        deny('cd-relative-read',
             "`cd` combined with a relative path (%s). The harness cannot resolve the target "
             "statically and will interrupt the user with a permission prompt.\n"
             "Use the Grep tool / Read tool with an absolute path, or drop `cd` and pass an "
             "absolute path, e.g. grep -n \"PAT\" ~/project/src/module.py" % ', '.join(bad))


def run_checks(cmd):
    check_irreversible(cmd)
    check_segment_rules(cmd)
    check_instruction_write(cmd)
    check_root_direct_write(cmd)
    check_inline_backslash(cmd)
    check_cd_relative_read(cmd)


def main():
    try:
        inp = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if not isinstance(inp, dict) or inp.get("tool_name") != "Bash":
        sys.exit(0)
    cmd = (inp.get("tool_input", {}) or {}).get("command", "") or ""
    if not isinstance(cmd, str) or not cmd:
        sys.exit(0)
    run_checks(cmd)
    sys.exit(0)


if __name__ == "__main__":
    main()
