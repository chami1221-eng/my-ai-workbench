#!/usr/bin/env python3
"""memory_index_lint.py - detect index lines that have turned into summaries,
and drift between index lines and the memory bodies they point to.

Three layers, three jobs:
  handoff      = what happened in this session and how to resume. Summaries are fine. Expires.
  body files   = decision + reason + the conditions under which it holds. Never summarize.
  index lines  = WHEN to open the body (a trigger). Never summarize.

When an index line becomes a summary, the agent decides from the index alone without
opening the body, and the conditions that made the decision valid are silently dropped.

Usage:
  python memory_index_lint.py                         # summary check
  python memory_index_lint.py --top 5
  python memory_index_lint.py --drift                 # index vs body drift
  python memory_index_lint.py --drift --file project_acme_city_analytics.md
  python memory_index_lint.py --memory-dir path/to/memory

Drift kinds:
  A  stale section pointer  the index says "open the YYMMDD section", the body has a newer one
  B  index-only value       an amount / stage / record id in the index is not in the body
  C  invalidated value      the body marked an old value as invalidated, the index still shows it

Memory dir resolution (first hit wins):
  --memory-dir  >  $CLAUDE_MEMORY_DIR  >  ~/.claude/projects/<project-key>/memory
  where <project-key> is the current working directory with every non-alphanumeric
  character replaced by "-" (the key Claude Code uses for per-project folders).
"""
import argparse
import os
import re
import sys
from pathlib import Path


def default_memory_dir():
    env = os.environ.get("CLAUDE_MEMORY_DIR")
    if env:
        return env
    key = re.sub(r"[^A-Za-z0-9]", "-", str(Path.cwd()))
    return str(Path.home() / ".claude" / "projects" / key / "memory")


# Phrases that send the reader to the body. If present, the index line works as a pointer.
POINTER = re.compile(
    r"本体|を開く|参照|節を開く|詳細は|前に開く"
    r"|\bopen\b|\bsee\b|\bbefore\b|\bbody\b|\bdetails? in\b|\bsection\b",
    re.I,
)
# Progress-log signals ("YYMMDD ... done / sent / completed")
PROGRESS = re.compile(
    r"\*?\d{6}\s*[^.。]{0,40}(済|した|実施|完了|送信済|\bdone\b|\bsent\b|\bcompleted\b|\bshipped\b)",
    re.I,
)
# Fact enumeration with slashes / middle dots / semicolons
ENUM = re.compile(r"[／/・;]")

SOFT_BYTES = 300   # target size of one index line
HARD_BYTES = 600   # beyond this the line is almost certainly a summary


def audit_line(line):
    """Return (score, reasons). Higher score = more summary-like."""
    b = len(line.encode("utf-8"))
    reasons = []
    score = 0
    if b > HARD_BYTES:
        score += 3
        reasons.append(f"{b}B (over HARD {HARD_BYTES})")
    elif b > SOFT_BYTES:
        score += 1
        reasons.append(f"{b}B (over SOFT {SOFT_BYTES})")

    n_enum = len(ENUM.findall(line))
    if n_enum >= 6:
        score += 2
        reasons.append(f"{n_enum} enumerations")
    elif n_enum >= 3:
        score += 1
        reasons.append(f"{n_enum} enumerations")

    n_prog = len(PROGRESS.findall(line))
    if n_prog:
        score += n_prog
        reasons.append(f"{n_prog} progress-log items")

    if b > SOFT_BYTES and not POINTER.search(line):
        score += 2
        reasons.append("no pointer to the body")
    return score, reasons


def index_files(memdir):
    return ["MEMORY.md"] + sorted(
        f for f in os.listdir(memdir) if f.startswith("index_") and f.endswith(".md")
    )


def collect(memdir):
    rows = []
    for fn in index_files(memdir):
        path = os.path.join(memdir, fn)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.rstrip("\n")
                if not line.startswith("- ["):
                    continue
                m = re.match(r"- \[([^\]]+)\]", line)
                name = m.group(1) if m else line[:30]
                score, reasons = audit_line(line)
                rows.append((score, fn, i, name, reasons, len(line.encode("utf-8"))))
    return rows


# ---- drift (index vs body) ----
LINK = re.compile(r"\]\(([^)]+\.md)\)")
# Section dates: "★260914" (original notation) or "*260914" / "§260914"
STAR = re.compile(r"[★§*]+\s*(2\d{5})")
HEAD = re.compile(r"^(#{2,4}\s.*?[★§*]+\s*(2\d{5}).*)$", re.M)
# Invalidated markers: 【無効260928】old -> new   or   [invalidated 260928] old -> new
INVALID_MARK = re.compile(r"(?:【無効\s*(\d{6})】|\[invalidated\s*(\d{6})\])([^\n]*)", re.I)
MONEY = re.compile(
    r"[¥￥]\s?(\d[\d,]*(?:\.\d+)?)\s*([MK万億]?)"
    r"|(\d[\d,]*(?:\.\d+)?)\s*(億|万)?円"
    r"|(\d[\d,]*(?:\.\d+)?)\s*(億|万)(?![人件名本枚回])"
    r"|\$\s?(\d[\d,]*(?:\.\d+)?)\s*([MK]?)"
)
STAGE = re.compile(r"Stage\s?[:：]?\s?0?(\d{1,2})", re.I)
UNIT = {"": 1, "K": 1_000, "M": 1_000_000, "万": 10_000, "億": 100_000_000}
ID_PATTERN = None  # compiled from --id-pattern


def _num(s, unit):
    return round(float(s.replace(",", "")) * UNIT[unit or ""])


def values(text):
    """Normalized value set, so that $48,000 / $48K compare equal."""
    out = {}
    for m in MONEY.finditer(text):
        yen1, u1, yen2, u2, yen3, u3, usd, u4 = m.groups()
        if yen1:
            key = ("jpy", _num(yen1, u1))
        elif yen2:
            key = ("jpy", _num(yen2, u2))
        elif yen3:
            key = ("jpy", _num(yen3, u3))
        else:
            key = ("usd", _num(usd, u4))
        if key[1] >= 1000:   # drop fragments such as "$3"
            out.setdefault(key, m.group(0).strip())
    for m in STAGE.finditer(text):
        out.setdefault(("stage", int(m.group(1))), m.group(0))
    if ID_PATTERN:
        for m in ID_PATTERN.finditer(text):
            out.setdefault(("id", m.group(0)), m.group(0))
    return out


def drift_line(line, memdir):
    files = [f for f in LINK.findall(line) if os.path.exists(os.path.join(memdir, f))]
    if not files:
        return []
    body = ""
    for f in files:
        with open(os.path.join(memdir, f), encoding="utf-8") as h:
            body += h.read() + "\n"
    hits = []

    # A: the body has a newer dated section than the one the index points to.
    idx_dates = STAR.findall(line)
    heads = HEAD.findall(body)
    if idx_dates and heads:
        newest = max(heads, key=lambda h: h[1])
        if newest[1] > max(idx_dates):
            sev = "high" if re.search(r"節|section", line, re.I) else "low"
            title = newest[0].lstrip("#").strip()[:50]
            hits.append((sev, "A", f"index *{max(idx_dates)} < body *{newest[1]} \"{title}\""))

    # C: invalidated old value (left of the arrow) still present in the index
    idx_vals = values(line)
    stale = {}
    for d1, d2, seg in INVALID_MARK.findall(body):
        old = re.split(r"→|->", seg)[0]
        for k, raw in values(old).items():
            stale[k] = (d1 or d2, raw)
    for k in idx_vals:
        if k in stale:
            hits.append(("high", "C", f"{idx_vals[k]} is invalidated in the body ({stale[k][0]})"))

    # B: index value missing from the body (excluding what C already caught)
    body_vals = values(body)
    miss = [raw for k, raw in idx_vals.items() if k not in body_vals and k not in stale]
    if miss:
        hits.append(("high", "B", "value not in body: " + ", ".join(miss)))
    return hits


def drift(memdir, only_files, verbose):
    n = 0
    shown = []
    for fn in index_files(memdir):
        path = os.path.join(memdir, fn)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if not line.startswith("- ["):
                    continue
                if only_files and not any(o in line for o in only_files):
                    continue
                n += 1
                for sev, kind, msg in drift_line(line.rstrip("\n"), memdir):
                    if sev == "high" or verbose:
                        m = re.match(r"- \[([^\]]+)\]", line)
                        shown.append((kind, sev, fn, i, m.group(1) if m else "?", msg))
    counts = {k: sum(1 for s in shown if s[0] == k) for k in "ABC"}
    print(f"index lines {n} / A(stale section) {counts['A']} / "
          f"B(index-only value) {counts['B']} / C(invalidated value) {counts['C']}")
    for kind, sev, fn, i, name, msg in sorted(shown, key=lambda s: (s[2] != "MEMORY.md", s[0])):
        tag = "" if sev == "high" else " (low)"
        print(f"  {kind}{tag} {fn}:{i}  {name[:30]}  - {msg}")
    if shown:
        print()
        print("Fix: A = update the section date in the index if the new section matters for its trigger /"
              " B = add the value to the body or drop it from the index / C = replace the old value in the index")
        print("When a value changes, do not delete the old one in the body. Write: "
              "[invalidated YYMMDD] old -> new")
    return 0


def main():
    global ID_PATTERN
    ap = argparse.ArgumentParser(description="Lint memory index lines (summary creep and drift).")
    ap.add_argument("--memory-dir", default=None,
                    help="memory directory (default: $CLAUDE_MEMORY_DIR or ~/.claude/projects/<cwd-key>/memory)")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--drift", action="store_true", help="check drift between index lines and bodies")
    ap.add_argument("--file", action="append", default=[],
                    help="limit --drift to index lines that point to this memory file (repeatable)")
    ap.add_argument("--verbose", action="store_true",
                    help="also show low-confidence A hits (lines that do not say 'section')")
    ap.add_argument("--id-pattern", default=None,
                    help="regex for record IDs to compare in --drift (e.g. '\\bOPP-\\d{5}\\b')")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    memdir = args.memory_dir or default_memory_dir()
    if not os.path.isdir(memdir):
        print("memory dir not found:", memdir)
        return 1
    if args.id_pattern:
        ID_PATTERN = re.compile(args.id_pattern)
    if args.drift:
        return drift(memdir, args.file, args.verbose)

    rows = collect(memdir)
    if not rows:
        print("no index lines found:", memdir)
        return 1
    bad = sorted((r for r in rows if r[0] > 0), key=lambda r: -r[0])
    total_bytes = sum(r[5] for r in rows)
    print(f"index lines {len(rows)} / total {total_bytes}B / suspected summaries {len(bad)}")
    print()
    print("score  file:line  name  - reasons")
    for score, fn, i, name, reasons, _b in bad[: args.top]:
        print(f"  {score:>2}   {fn}:{i}  {name[:34]}  - {' / '.join(reasons)}")
    if bad:
        print()
        print("Fix: move the facts into the body, keep only WHEN to open it in the index")
        print('  bad : "A decided / B confirmed / C not allowed / D has 37 items"')
        print('  good: "Before deciding the on-site logistics (handouts, recording), open the *260914 section"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
