#!/usr/bin/env python3
"""hook-corpus-regression / Step 1: extract a corpus of real past tool calls.

Sources:
  --transcripts      Claude Code transcripts: ~/.claude/projects/*/*.jsonl, tool_use blocks' `input`
  --static  fenced code blocks in skill docs (*.md), i.e. commands an agent is likely to copy

Output: one JSON object per line
  {"tool_name": "Bash", "tool_input": {...}, "src": "<file>", "ts": "..."}

Exits non-zero on 0 items, so an extraction failure is never read as "0 blocks = pass".
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
from datetime import datetime, timedelta, timezone

HOME = pathlib.Path.home()
PROJECTS = pathlib.Path(os.environ.get('CORPUS_PROJECTS_DIR', str(HOME / ".claude" / "projects")))
# Extra skill roots: os.pathsep-separated list in CORPUS_SKILLS_DIRS
SKILLS_DIRS = [HOME / ".claude" / "skills"] + [
    pathlib.Path(os.path.expanduser(p))
    for p in os.environ.get("CORPUS_SKILLS_DIRS", "").split(os.pathsep) if p.strip()
]
FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n(.*?)```", re.S)


def iter_transcripts(tools, since):
    for jsonl in sorted(PROJECTS.glob("*/*.jsonl")):
        try:
            with jsonl.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or '"tool_use"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    ts = rec.get("timestamp") or ""
                    if since and ts and ts < since:
                        continue
                    content = (rec.get("message") or {}).get("content")
                    if not isinstance(content, list):
                        continue
                    for blk in content:
                        if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                            continue
                        name = blk.get("name") or ""
                        if tools and name not in tools:
                            continue
                        yield {"tool_name": name, "tool_input": blk.get("input") or {},
                               "src": str(jsonl), "ts": ts}
        except OSError:
            continue


def iter_static(tools):
    for root in SKILLS_DIRS:
        if not root.exists():
            continue
        for md in root.rglob("*.md"):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for block in FENCE.findall(text):
                block = block.strip()
                if block:
                    yield {"tool_name": (sorted(tools)[0] if tools else "Bash"),
                           "tool_input": {"command": block}, "src": str(md), "ts": ""}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--transcripts", action="store_true", help="Claude Code transcript tool calls")
    ap.add_argument("--static", action="store_true", help="fenced code blocks in skill docs")
    ap.add_argument("--tools", default="", help="comma-separated tool-name filter")
    ap.add_argument("--since", default="", help="ISO date or e.g. 30d")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-dedupe", action="store_true")
    args = ap.parse_args()

    if not (args.transcripts or args.static):
        sys.exit("need --transcripts and/or --static")
    tools = {t.strip() for t in args.tools.split(",") if t.strip()}
    since = args.since
    m = re.fullmatch(r"(\d+)d", since or "")
    if m:
        since = (datetime.now(timezone.utc) - timedelta(days=int(m.group(1)))).isoformat()

    seen, n = set(), 0
    out = pathlib.Path(os.path.expanduser(args.out))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        sources = []
        if args.transcripts:
            sources.append(iter_transcripts(tools, since))
        if args.static:
            sources.append(iter_static(tools))
        for src in sources:
            for item in src:
                if not args.no_dedupe:
                    key = hashlib.sha1(json.dumps(item["tool_input"], sort_keys=True,
                                                  ensure_ascii=False).encode("utf-8")).hexdigest()
                    if key in seen:
                        continue
                    seen.add(key)
                fh.write(json.dumps(item, ensure_ascii=False) + "\n")
                n += 1
                if args.limit and n >= args.limit:
                    break
            if args.limit and n >= args.limit:
                break

    print(f"extracted {n} items -> {out}", file=sys.stderr)
    if n == 0:
        sys.exit("0 items. Do not read an extraction failure as a pass.")


if __name__ == "__main__":
    main()
