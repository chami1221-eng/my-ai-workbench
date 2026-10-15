---
name: hook-corpus-regression
description: >
  Regression-test a new or changed PreToolUse hook against the corpus of tool calls
  that actually ran in past sessions, and confirm it produces zero false blocks before
  it goes live. Synthetic tests never find these false positives.
  Use whenever: a hook was created / a hook's regex, matcher or condition changed /
  "will this hook block my normal work?".
  Triggers: "hook regression", "hook-corpus-regression", "false block check", "corpus regression".
  Do NOT use: while still deciding what a hook should do (that is design) / for a single
  smoke test (pipe one payload by hand) / for non-hook scripts.
---

# hook-corpus-regression

## Why

The danger of a hook is not that it fails to block. It is that it blocks something it
should not. The vicious cycle this risks:

```
1. a guard blocks a legitimate operation
2. the agent concludes "this whole category is blocked"
3. the agent tries to evade it (path obfuscation, string splitting)
4. the evasion becomes habit, and the guard is meaningless
```

The only reliable fix is to never cause step 1, and step 1 does not show up in synthetic
tests: real commands are far messier and more varied than anything you would invent.
So the gate for deploying a hook is: **replay every real past call, get 0 false blocks.**

## Where the corpus lives

| Source | Location |
|---|---|
| Claude Code transcripts | `~/.claude/projects/<key>/*.jsonl`, `message.content[] type=tool_use`, field `input` |
| Static | fenced code blocks in `SKILL.md` / `references/*.md` (commands the agent will copy). Your own docs can trip your own hook |

## Steps

### 1. Extract

```bash
python scripts/extract_corpus.py --out /tmp/corpus_transcripts.jsonl --transcripts --tools Bash --since 30d
python scripts/extract_corpus.py --out /tmp/corpus_static.jsonl --static --tools Bash
```

`--transcripts` scans all project transcripts and de-duplicates identical payloads.
**If it returns 0 items, stop.** Reading an extraction failure as "0 blocks, pass" is the worst outcome.

### 2. Judge

```bash
# a single hook
python scripts/judge.py --hook ../hooks/check_mcp_deletion.py \
  --corpus /tmp/corpus_transcripts.jsonl --report /tmp/report_mcp.md

# the whole dispatcher (the real production entry point)
python scripts/judge.py --hook ../hooks/pretool_hooks.py \
  --corpus /tmp/corpus_transcripts.jsonl --force-tool Bash --report /tmp/report_all.md
```

- Reasons that contain `rule=<name>` are grouped by rule. Others are grouped by the first 40 chars.
- Details stop at 200 items; the "by rule" section counts everything.
- Hooks with an observe mode: pass `--env KEY=enforce`, or you will see 0 blocks.

In-process mode is the default and is much faster:

| mode | 893 items | 8,077 items |
|---|---|---|
| in-process (default) | 8.7 s | 20.8 s |
| `--subprocess` | 132.5 s | timed out after 10 min |

Both modes produced identical results on 893 items (same blocks, same rule breakdown).
A dispatcher runs several checks per call, so expect roughly 10x the single-hook time
(measured: 8,095 Bash calls through the dispatcher in 207.9 s).

### 3. Label every block by hand

Do not wave this through.

| label | meaning | action |
|---|---|---|
| true positive | it really should be stopped | keep, but confirm an alternative path exists |
| false positive | it should not have been stopped | narrow the condition, rerun step 2 |

### 4. Deployment checklist

- [ ] every block in the real corpus is a true positive (0 false positives)
- [ ] every blocked operation has a working alternative (a helper, a tool) so work can continue
- [ ] no snippet takes over 1 s (regex backtracking)
- [ ] unexpected payloads (empty, None, str, list, huge, non-UTF-8) do not crash it
- [ ] HOOK-EXCEPTION is 0 (Claude Code fails open on hook errors: an exception is a silent hole)

## Typical false-positive patterns (check these first)

**A. Combined conditions written as whole-string AND.**
"API host appears anywhere" AND "write method appears anywhere" fires when one script
does a read and a write far apart. Require proximity (e.g. within 240 chars) instead.

**B. A read endpoint that uses a write verb.**
Some read APIs use POST (free/busy lookups, search endpoints). Neutralize the endpoint
and path as a pair, not a single word.

**C. Forbidden words quoted in documentation.**
A doc line saying "sending via messages/send is forbidden" is not a call. Add an HTTP
context condition (`urllib`, `requests.`, `curl`) nearby.

**D. Allowlists evaluated on the whole line.**
`echo helper.py; curl https://api...` or `... # helper.py` would exempt a direct call
because the allow token appears somewhere on the line. Evaluate allow tokens per segment, with
comments stripped (see `bash_guard.py`).

**E. Line-based vs whole-string regex.**
`[^|;&]*` matches newlines in Python but not in `grep`. Porting a grep rule to `re.search`
on the whole command makes it span lines and over-block multi-line python (5 cases in the corpus).

## Staged rollout

Passing the corpus is not the last step.

1. Give the hook an observe mode (decide, but only log)
2. Run observe for a few working days; confirm 0 false positives in live logs
3. Switch to enforce. If the mode is a constant in the script, no restart is needed
   (hooks are spawned per call; only changing the registration needs a restart)

## Track record

- New guard for a second agent runtime: 893 real calls (44 days) -> 7 blocks (0.8%), all
  true positives. The static corpus found 2 false positives (patterns A and B), fixed
  before deployment.
- Existing write-format guard, replayed later: 8,077 real calls -> 10 blocks (0.12%), all
  true positives (raw writes that skip the required helper).
- Full dispatcher (current version): 8,095 Bash calls -> 279 blocks (3.45%), 0 hook exceptions.
