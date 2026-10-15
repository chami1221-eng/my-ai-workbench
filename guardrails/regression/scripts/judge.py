#!/usr/bin/env python3
"""hook-corpus-regression / Step 2: feed a corpus to a hook and count blocks.

Default mode is in-process (stdin/stdout swapped, hook run via runpy in this process).
Process spawns are expensive on Windows, and thousands of subprocess runs are impractical.
Use --subprocess only when you need the exact production execution shape.

Block detection (Claude Code PreToolUse contract):
  - exit code 2                                   -> block
  - stdout JSON permissionDecision == "deny"      -> block
  - stdout JSON decision == "block"               -> block
A hook that raises is counted separately as HOOK-EXCEPTION: Claude Code fails open on
hook errors, so in production that is a silent hole. Not a pass unless it is 0.
"""
import argparse
import io
import json
import os
import pathlib
import re
import runpy
import subprocess
import sys
import time

RULE_RE = re.compile(r"rule[=:]\s*([A-Za-z0-9_.\-]+)")


def decide(exit_code, stdout, stderr):
    if exit_code == 2:
        return True, (stderr or stdout or "").strip()
    text = (stdout or "").strip()
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except ValueError:
            return False, ""
        hso = obj.get("hookSpecificOutput") or {}
        if hso.get("permissionDecision") == "deny":
            return True, str(hso.get("permissionDecisionReason") or "")
        if obj.get("decision") == "block":
            return True, str(obj.get("reason") or "")
    return False, ""


def run_in_process(hook_path, payload):
    old_in, old_out, old_err, old_argv = sys.stdin, sys.stdout, sys.stderr, sys.argv
    sys.stdin = io.StringIO(json.dumps(payload, ensure_ascii=False))
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    sys.argv = [str(hook_path)]
    code, exc = 0, None
    try:
        runpy.run_path(str(hook_path), run_name="__main__")
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException as e:  # noqa: BLE001 - count hook exceptions, do not crash
        exc = f"{type(e).__name__}: {e}"
        code = -1
    finally:
        out, err = sys.stdout.getvalue(), sys.stderr.getvalue()
        sys.stdin, sys.stdout, sys.stderr, sys.argv = old_in, old_out, old_err, old_argv
    return code, out, err, exc


def run_subprocess(hook_path, payload):
    try:
        p = subprocess.run([sys.executable, str(hook_path)],
                           input=json.dumps(payload, ensure_ascii=False),
                           capture_output=True, text=True, encoding="utf-8", timeout=30)
        return p.returncode, p.stdout, p.stderr, None
    except subprocess.TimeoutExpired:
        return -1, "", "", "Timeout(30s)"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hook", required=True)
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--report", default="")
    ap.add_argument("--force-tool", default="", help="override the corpus tool name")
    ap.add_argument("--payload-key", default="tool_input")
    ap.add_argument("--env", action="append", default=[], help="KEY=VAL passed to the hook")
    ap.add_argument("--subprocess", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    hook = pathlib.Path(os.path.expanduser(args.hook))
    if not hook.exists():
        sys.exit(f"hook not found: {hook}")
    corpus = pathlib.Path(os.path.expanduser(args.corpus))
    items = [json.loads(l) for l in corpus.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        items = items[: args.limit]
    if not items:
        sys.exit("corpus is empty. Re-extract; 0 items is not a pass.")
    for kv in args.env:
        k, _, v = kv.partition("=")
        os.environ[k] = v

    runner = run_subprocess if args.subprocess else run_in_process
    blocks, slow, excs, rules = [], [], [], {}
    t0 = time.time()
    for it in items:
        payload = {
            "tool_name": args.force_tool or it.get("tool_name") or "Bash",
            args.payload_key: it.get("tool_input") or {},
            "session_id": "hook-corpus-regression",
            "cwd": os.getcwd(),
        }
        t1 = time.time()
        code, out, err, exc = runner(hook, payload)
        dt = time.time() - t1
        if dt > 1.0:
            slow.append((dt, it))
        if exc:
            excs.append((exc, it))
            continue
        blocked, reason = decide(code, out, err)
        if blocked:
            m = RULE_RE.search(reason or "")
            rule = m.group(1) if m else " ".join((reason or "(no reason)").split())[:40]
            rules[rule] = rules.get(rule, 0) + 1
            blocks.append((rule, reason, it))
    elapsed = time.time() - t0

    mode = "subprocess" if args.subprocess else "in-process"
    lines = [
        "# hook regression report", "",
        f"- hook: `{hook}`",
        f"- corpus: `{corpus}` ({len(items)} items)",
        f"- mode: {mode} / {elapsed:.1f}s",
        f"- **blocks {len(blocks)} ({len(blocks)/len(items)*100:.2f}%)**",
        f"- HOOK-EXCEPTION {len(excs)} / over 1s {len(slow)}",
        "", "## by rule",
    ]
    for rule, n in sorted(rules.items(), key=lambda x: -x[1]):
        lines.append(f"- {rule}: {n}")
    if not rules:
        lines.append("- (no blocks)")
    lines += ["", "## block details (a human labels each one true/false positive)"]
    for rule, reason, it in blocks[:200]:
        snippet = json.dumps(it.get("tool_input"), ensure_ascii=False)[:300]
        lines += ["", f"### [{rule}] {it.get('src', '')}", f"- reason: {reason[:300]}", f"- input: `{snippet}`"]
    if excs:
        lines += ["", "## HOOK-EXCEPTION (silent hole in production; fix all)"]
        for exc, it in excs[:50]:
            lines.append(f"- {exc} / {json.dumps(it.get('tool_input'), ensure_ascii=False)[:200]}")
    if slow:
        lines += ["", "## over 1s (suspect regex backtracking)"]
        for dt, it in sorted(slow, key=lambda x: -x[0])[:20]:
            lines.append(f"- {dt:.2f}s / {json.dumps(it.get('tool_input'), ensure_ascii=False)[:200]}")

    report = "\n".join(lines) + "\n"
    if args.report:
        rp = pathlib.Path(os.path.expanduser(args.report))
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(report, encoding="utf-8")
        print(f"report -> {rp}", file=sys.stderr)
    print("\n".join(lines[:14]))
    sys.exit(1 if excs else 0)


if __name__ == "__main__":
    main()
