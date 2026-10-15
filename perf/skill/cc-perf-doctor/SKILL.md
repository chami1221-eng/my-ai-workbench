---
name: cc-perf-doctor
description: >
  Find out why Claude Code is slow by measuring three layers: hook blocking time / async CPU /
  prompt-prefix token breakdown. Measure before touching anything.
  Use whenever: Claude Code feels slow or heavy / before and after adding a hook /
  to check the effect of turning skills or MCP servers on or off / right after upgrading
  Claude Code, to retake the baseline.
  Triggers: "Claude Code is slow", "slow responses", "hook cost", "cc-perf-doctor", "prefix",
  "slow startup".
  Do NOT use: the whole PC is slow or zombie processes, disk full, compressing MEMORY.md or
  CLAUDE.md content, wrapping up a long conversation.
---

# cc-perf-doctor: three layers of Claude Code slowness

> **No guessing. Measure before reading commands and theorizing.**
> **A hook's cost is dominated by how many processes it starts**, not by how heavy each one
> is. Fold several checks into one process.

Each layer has its own instrument. **Go top-down** (lower layers pay off less).

| Layer | What to look at | Tool | Payoff |
|---|---|---|---|
| **1. Hooks** | Seconds per firing (blocking and async separately) | `tools/hook-perf-audit.py` | **Largest.** Real result: 32.4s -> 3.8s |
| **2. Tool call count** | How many calls, not how big each output is | session transcript (jsonl) counts | Large. Same for Claude Code and Codex |
| **3. Prefix** | Token breakdown of what loads at session start | `tools/prefix_baseline.py` | Medium. With prompt caching, little latency effect |

---

## Layer 1: hooks (first)

### 1-1 Measure first

```bash
python tools/hook-perf-audit.py          # median of 3 runs + diff vs previous run
python tools/hook-perf-audit.py --quick  # 1 run (when in a hurry; NOT for judging)
python tools/hook-perf-audit.py --list   # parse and list hooks without running them
```

Output: measured seconds **per matcher** (not per event: a Write only runs the matcher=Write
hooks) / **async in a separate bucket** / process count / **duplicate registration with
settings.local.json** / always-on injected bytes / number of `<important if>` blocks /
**diff vs the previous run**.

### Measurement rules (miss these and every conclusion shifts)

1. **Process start cost is not one number.** Measured ranges:
   `cmd 0.5-0.8s / bash 0.6-1.5s / python 0.8-1.6s / jq 0.5-2.1s /`
   **`pwsh 2.7-3.0s (about 3x python)`**. A single figure such as "1.3-1.5s" hides the range.
2. **Values swing about +/-40% with the number of concurrent sessions.**
   **Judge improvements by before/after with the same script; never compare absolute values
   across days.** The tool keeps the previous run in a state file and prints the diff.
   **A `--quick` diff is reference only** (comparing single runs flags red every time).
3. **Do not measure through `shell=True` (cmd.exe).** `$HOME` is not expanded, so you measure
   "file not found, exit immediately" and **underestimate cost 3-10x**. The audit tool calls
   bash directly for this reason.
4. **Do not add `async: true` hooks to the total.** They do not block the reply; adding them
   counts cost that is not paid in latency.

Verdicts and fixes:

| Symptom | Fix |
|---|---|
| An event over budget (UserPromptSubmit 1.0s / PreToolUse, PostToolUse 0.8s / Stop 2.0s) | **Do not delete hooks; fold them into one process** or make them async. Precedent: three UserPromptSubmit hooks folded into one dispatcher, **5.85s -> 0.27s** |
| **PreToolUse / PostToolUse matcher is `*` (all tools)** | **List the target tools.** Structural waste even within budget. This was the biggest single win: Read / Grep / Glob went to zero hooks, **Read 4.8s -> 0.0s** |
| A `bash + jq` hook | Rewrite as one Python process. One jq call is about 1.4s. An old injector with bash + 2 jq took 4.7s (18s outlier) |
| A blocking `pwsh` hook | If it only notifies, `"async": true`. pwsh costs about 3x python |
| **Old standalone hooks in settings.local.json next to a dispatcher that already runs them** | Risk: **the always-on rules get injected twice per turn**. Remove the standalone copies (one audit found 19) |
| Always-on injection over 40KB / more than 12 `<important if>` blocks | Move content to on-demand reference files |

### Async is "do not sum it", not "ignore it"

**Async leaves the latency path, but CPU is still paid on every start.**
The audit prints async separately. **When the async bucket exceeds 3 seconds, fix it.**

> Example: a 9.73s async SessionStart is easy to write off as "does not block, so the user
> does not feel it". Folding it brought it to **0.56s (-94%)**.
> "Not felt" does not mean "does not need fixing".

Same order as blocking hooks (fold, reduce frequency, remove). Real cases:

| Target | Before | After | How |
|---|---:|---:|---|
| context injector (bash + grep/sed/awk/ls/wc, about 16 processes) | 7.04s | **0.19s** | **Folded into one Python process** |
| zombie MCP cleanup (pwsh) | 2.69s | **0s** | **24-hour gate**, fired detached from the injector (already async, so no extra cost; 2 hooks -> 1) |

### Check what a hook actually does, not only what it costs

**Hooks added "for safety" are the ones nobody verifies.** Look at the log, not only the
seconds.

```bash
L="$TEMP/cleanup-zombie-mcps.log"
wc -l < "$L"                      # total runs
grep -c 'orphans=0' "$L"          # runs that did nothing
grep -c 'killed=[1-9]' "$L"       # runs that actually helped
```

Real measurement: **40 runs, orphans=0 in all 40, 0 kills**, at about 3x python cost per start.

- **Judge by record, not by claim.** "No zombies appeared" was not this hook's doing (it killed
  nothing); they simply did not occur.
- **Zero effect is not an automatic delete.** Reducing frequency and keeping it as insurance is
  the default (it was cut to once a day). Removal is the owner's call.
- **A resident hook that logs nothing cannot be evaluated.** Add at least one log line.

### Folding a bash hook into Python without breaking it

Folding is right, but measured lessons buried in the bash script's comments can drop out in a port.
In one port, four lived only in comments: locale-dependent sort produces a meaningless diff
every run / counters must match the real unit words / **dropping CRLF makes every line differ
even when content is identical**, burying real changes / never write back an empty result.
**Read every comment before porting. Do not copy only the logic.**

Verify in two steps (one is not enough):

1. The port's `--check` judges the existing output as up to date (byte match).
2. Let it actually write, and confirm the sha256 is unchanged (catches CRLF, ordering, wording).

**Keep the old script** (for manual runs and `--check`).

### When you change hooks, apply the same change to settings.local.json

Measured: SessionStart had the same three hooks in both settings files. Identical entries are
deduplicated, so that is not waste. Risk: **fixing only one side** can replace a
user-level hook block instead of merging it per matcher, and the hooks go silent with no error. **Do not "delete one side". Make both sides identical.**

### Do not touch

- **Exact duplicate hooks are not waste.** Claude Code deduplicates identical hook commands
  (documented, and confirmed: an access counter incremented by 1, not 2).
- **Do not solve it by deleting hooks.** Deleting removes guards (send-message deny, heredoc
  deny, destructive-update deny). Use the three moves: **fold / async / narrow the matcher**.
- **Settings writes may be blocked by the auto-mode classifier** (hook-reducing edits are a
  typical target). Show a dry-run and let the human apply it. Additive changes such as async usually
  pass.

### Mechanical findings are candidates. Read the reference before cutting

A blunt duplicate rule ("if a dispatcher exists, every local hook is a duplicate") flags
independent guards too. In one scan, 6 items were flagged as "needs decision"; reading the
references showed 4 were not calls at all (a related skills list, a "Do NOT use" line, a
deny-coverage test table). **Both directions of error are possible.**

**When adding a new hook, add it to an existing dispatcher, not as a new settings entry.**

| Event | Dispatcher pattern | How to add |
|---|---|---|
| UserPromptSubmit | `prompt-hooks.py` | one line in `PARTS`: `('name', function)`; the part exposes `build_message(payload) -> str or None` |
| PreToolUse | `pretool-hooks.py` | one line in `PARTS`: `('name', path, {target tools})`; the part runs unmodified via `runpy` as `__main__` |
| Stop | `stop-hooks.py` | same; notifiers are detached |

A new `.py` added directly to settings costs **a fixed +0.3-0.5s every time**. After adding,
run the dispatcher's self-test (does the deny actually fire?) and a wiring check (which part
runs for which matcher).

---

## Layer 2: tool call count

**Reducing the number of calls beats shrinking each output.**

Measured over 3 days: **Bash tool output 895,048 tokens over 2,528 calls.** Each call is small;
there are many. Lowering the Bash output cap was rejected: Bash p99 was 8,601 characters,
already well inside the default 30,000, so it would change nothing.

- **Moving work to Codex does not fix this structurally.** Codex has its own session start,
  rule injection, pre-tool deny and post-tool audit. Hooks per command differ (4 vs 2), but the
  reliable lever is fewer calls, in either tool.
- Reference: sessions that delegated execution to Codex CLI showed 34% lower billed tokens per
  message (execution logs do not land in Claude Code's context), but output per message rose
  (writing the instructions costs tokens).

---

## Layer 3: prefix (tokens loaded at start)

### 3-1 Split the prefix in three

**Bytes alone do not tell you what to cut.** Split the first turn's real tokens:

```bash
python tools/prefix_baseline.py --floor <safe-mode tokens> --injected <always-on tokens>
```

One real measurement:

| Part | tokens | Lever |
|---|---:|---|
| Claude Code floor (measured in safe mode) | 25,625 | **Cannot be reduced from your side** |
| Always-on injection (CLAUDE.md / rules / MEMORY.md / Layer 0) | 39,369 | Move by audience / zone |
| **Skill list + deferred tool names** | about 26,112 | Read the notes below first |
| **Total** | **91,121** | |

- **Upgrading Claude Code does not shrink it on its own.** One minor upgrade changed the prefix
  by **+49 tokens**, although release notes suggested a section had been shortened.
  **Verify release-note claims by measurement before relying on them.**
- **Cutting skills does not always shrink it.** In Codex CLI, **152 -> 51 skills saved only
  460 tokens** (each remaining description grew from 92 to 220 characters, refilling the space;
  a budget-style allocation is suspected). **Not verified for Claude Code. Take a baseline
  before cutting and measure again in a new session after.**
- **The skill list is baked into the prefix: no effect in the same session.** After changing
  skill overrides, measure in the **next** session.
- **Check load-bearing references before turning a skill off** (all SKILL.md, CLAUDE.md, rules,
  agent definitions, scripts, Layer 0). **A usage count of 0 does not mean unused**: calling the
  skill's script directly does not increment the counter.
- Turning a skill off keeps its scripts, so direct-path calls still work. Only `Skill()` and
  the listing disappear.

### 3-2 Order of reduction (largest effect first)

1. **Hooks** (layer 1). Direct latency. With caching, injection cuts barely affect latency.
2. **Number of tool calls.**
3. **Always-on injection.** Do not "cut"; **relocate by audience / zone**. Precedents: a 19KB
   Codex-only section moved to a file injected only for Codex; 4.4KB of workbook-generation rules
   moved to the CLAUDE.md of the folder where that work happens.
4. **Skill list.** After reading the budget caveat above.

**Rejected (do not do):** removing MCP servers to save tokens (**deferred tools keep their
schemas out of the prefix, so it has no effect**, and editing the MCP server config carries a
real risk of a destructive merge) / setting a lower Bash output cap (p99 is already inside the
default).

---

## Common misdiagnoses

| Misdiagnosis | Reality |
|---|---|
| "The model is slow" | **Process start tax.** An empty `bash -c :` costs 0.6-1.5s; `pwsh -NoProfile -Command exit` costs 2.7-3.0s |
| "Duplicate hooks are waste" | **Deduplicated by Claude Code**, run once. Do not delete |
| "Upgrading will make it lighter" | **+49 tokens** of prefix after an upgrade. Measure release-note claims |
| "Fewer MCP servers means fewer tokens" | **Deferred tools keep schemas out of the prefix.** No effect, real risk |
| "Async, so not felt, so no need to fix" | **CPU is paid on every start.** 9.73s -> 0.56s once folded |
| "Small transcript in bytes means light" | Transcript bytes are raw tool output, not tokens. A 2.21MB transcript was at 41.4% real context use |
| "The audit says within budget" | **Check the instrument.** Measuring through `shell=True` underestimates 3-10x |
