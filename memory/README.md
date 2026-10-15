# Long-term memory for a coding agent

How I keep Claude Code's file-based memory accurate over months of daily use, and the tools
that check it.

## The problem

An agent with persistent memory fails in three quiet ways:

1. **It forgets.** A memory file with no index line is never auto-recalled. It still exists,
   but only grep can reach it.
2. **It acts on stale facts.** A value changes in the body, the index line still shows the old
   one, and the agent quotes the index without opening the body.
3. **Summaries drop conditions.** The auto-loaded index has a byte budget (Claude Code loads
   `MEMORY.md` only up to roughly 200 lines or 25KB). Pressure to fit pushes facts into index
   lines, and a one-line summary keeps the conclusion while losing the "only if" that made it
   true.

Comparing the index with the files as two sets once showed 284 indexed names, 206 files, and
only 165 (51%) present in both. A freshness scan does not notice this, because it only asks
"is this old?", not "does this exist?".

## The design

Full write-up: [docs/memory-architecture.md](docs/memory-architecture.md).

- **Three layers.** Handoff notes (may be summarized, expire), body files (decision + reason +
  conditions, never summarized), index lines (when to open the body, never summarized).
- **Index lines are triggers, not facts.** "Before quoting or changing scope, open the 260928
  section" instead of a list of numbers.
- **Invalidate, do not delete.** `[invalidated 260928] old $48,000 -> new $12,000`. The history
  stays, and a lint can detect the old value lingering in the index.
- **Two-tier index.** `MEMORY.md` is a small router plus about 15 HOT lines; domain
  sub-indexes hold everything else and are read on demand.
- **Two-way verification.** Orphans (file, no index) and ghost links (index, no file) are both
  checked, and ghosts named by files injected every turn are fixed first.
- **Promotion and demotion with a reason.** Each index line can carry
  `<!-- why:... since:YYMMDD -->`, so a later GC does not undo a deliberate demotion.

## The tools

| Path | What it does |
|---|---|
| `tools/memory_index_lint.py` | Scores index lines for summary creep (bytes, enumerations, progress logs, missing pointer). `--drift` finds index/body disagreement: stale section pointer (A), value only in the index (B), invalidated value still in the index (C) |
| `skill/memory-index-repair/` | Claude Code skill. `scan.py` finds orphans and ghosts and ranks ghosts by whether they are injected every turn; `propose.py` drafts index-line material for orphans; `filecheck.py` checks that file paths named inside memories exist (and detects files that moved) |
| `examples/memory/` | A synthetic memory folder (fictional "ACME City" deal) with deliberate defects so every check has something to find |

## How to run

Python 3.9+, standard library only.

```bash
# memory dir defaults to $CLAUDE_MEMORY_DIR, then ~/.claude/projects/<cwd-key>/memory
python tools/memory_index_lint.py --memory-dir examples/memory
python tools/memory_index_lint.py --memory-dir examples/memory --drift
python skill/memory-index-repair/scripts/scan.py --memory-dir examples/memory --cite-file examples/memory/layer0.md
python skill/memory-index-repair/scripts/propose.py --memory-dir examples/memory
python skill/memory-index-repair/scripts/filecheck.py --memory-dir examples/memory
```

Expected findings on the example folder:

- lint: the "Example Corp pilot" line is flagged (370B, 11 enumerations, progress log, no pointer)
- drift: ACME City line points to the 260910 section while a 260928 section exists (A), and
  shows $48,000, which the body invalidated (C)
- scan: 4 ghost links, 3 ranked as injected every turn (one is cited only by `layer0.md`),
  1 orphan (`reference_orphan_demo.md`)
- filecheck: 1 GHOST path (the workshop deck that was never created)

To use the skill in Claude Code, copy `skill/memory-index-repair/` to `~/.claude/skills/`.
