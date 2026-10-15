# cc-perf-doctor: measure Claude Code slowness before touching it

When Claude Code feels slow, the tempting fixes are guesses: blame the model, delete hooks,
remove MCP servers, upgrade. Measured, most of those guesses do not hold. This folder
holds the skill and the two scripts I use to measure first.

## Rule: measure before touching

- Judge a change by **before/after on the same machine with the same script**. Absolute values
  swing about +/-40% with the number of concurrent sessions, so do not compare across days.
- Check the instrument too. Measuring hooks through `cmd.exe` leaves `$HOME` unexpanded and
  reports costs 3-10x too low. "Within budget" means nothing until the instrument is right.

## Three layers, top-down

| Layer | Question | Tool | Example result |
|---|---|---|---|
| 1. Hook blocking time | How many seconds does each hook firing add, per matcher? | `tools/hook-perf-audit.py` | 32.4s -> 3.8s total; Read 4.8s -> 0.0s by narrowing a `*` matcher |
| 1b. Async CPU | Non-blocking hooks still burn CPU on every start. Over budget? | same tool, separate bucket | SessionStart async 9.73s -> 0.56s |
| 2. Tool call count | How many calls, not how large each output is | session transcript counts | 2,528 Bash calls / 895,048 tokens in 3 days; p99 output was already under the cap |
| 3. Prompt-prefix tokens | What is in the first-turn prefix: Claude Code floor, always-on text, skill list | `tools/prefix_baseline.py` | 25,625 floor + 39,369 always-on + about 26,112 skills/tool names = 91,121 |

The main finding: a hook's cost is dominated by how many processes it starts, so fold several
checks into one process. The fixes that worked were to **fold** several hooks into one Python
dispatcher, make notifiers **async**, and **narrow matchers**. Deleting hooks is not the answer (it removes safety guards), and exact duplicate hooks are already
deduplicated by Claude Code.

Full method, thresholds and common misdiagnoses: [skill/cc-perf-doctor/SKILL.md](skill/cc-perf-doctor/SKILL.md).

## How to run

Python 3.9+, standard library only. Hooks are executed through bash (Git Bash on Windows is
auto-detected).

```bash
# List hooks from settings without running them
python tools/hook-perf-audit.py --list

# Measure (median of 3 runs per hook), print verdict and diff vs the previous run
python tools/hook-perf-audit.py
python tools/hook-perf-audit.py --settings path/to/settings.json --claude-md path/to/CLAUDE.md --rules-dir path/to/rules

# Check that the async warning actually fires (silent and broken look the same)
HOOK_ASYNC_BUDGET=0 python tools/hook-perf-audit.py --no-save

# Prefix breakdown of the current (or newest) session
python tools/prefix_baseline.py
python tools/prefix_baseline.py <session-id> --floor 25625 --injected 39369 --previous 91072
```

Measuring runs your real hook commands with a sample payload. Use `--list` first if any hook
has side effects.

To use the skill in Claude Code, copy `skill/cc-perf-doctor/` to `~/.claude/skills/` and the
scripts to wherever the skill expects them (paths in SKILL.md are relative to this folder).
