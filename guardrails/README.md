# Guardrails: Claude Code PreToolUse hooks + corpus regression

A rule in the prompt is a wish; a hook is a guarantee.

Every check here started as a sentence in an instruction file. A sentence in the prompt
only works if the model remembers it at the right moment, even when it is loaded on every
turn. The hook version runs before the tool call and does not depend on the model remembering.

## What each hook does

| Hook | Blocks / warns | Risk it addresses |
|---|---|---|
| `bash_guard.py` inline-backslash | deny: `\` inside a heredoc body or `python -c` | The shell halves backslashes before python sees them. `C:\dir\2026` turns `\2` into an octal escape and the corrupted path is saved with no error. |
| `bash_guard.py` cd-relative-read | deny: `cd dir && grep x relative.file` | Not dangerous, but the harness cannot resolve paths after `cd`, so it interrupts the user with a permission prompt even in bypass mode. Absolute paths never reach a human. |
| `bash_guard.py` irreversible actions | deny: configurable patterns, example = setting a CRM deal to Closed Lost | A CRM stage change that cannot be undone (for example, closing a deal that still has an approved quote attached) should be clicked by a human. The agent prepares everything else and the human clicks the final stage. |
| `bash_guard.py` segment rules | deny: direct API calls that must go through a helper; public posts that must stay drafts | An allowlist evaluated on the whole line can be bypassed by `echo helper.py; curl ...` or by putting the helper name in a `# comment`. Allow tokens count only in the same segment, comments stripped. |
| `bash_guard.py` instruction / root writes | ask: shell writes to protected instruction files or straight into `~/Documents` | Covers the gap left by Write/Edit hooks: shell redirects, `tee`, `cp`, python `open(..., 'w')`. |
| `check_mcp_deletion.py` | deny: Write/Edit that drops existing `mcpServers` keys or leaves the config as invalid JSON | Risk: a settings edit can silently drop an existing MCP server entry. Nothing fails at that moment; the server is just gone the next time it is needed. |
| `check_chat_send_deny.py` | deny: immediate chat send. warn: scheduled send. pass: draft | Risk: a permission dialog that does not show the message body is not a real approval of the text. The agent may only draft; the human sends. |
| `check_memory_value_drop.py` | deny: edits to memory notes that remove money amounts, stages or record IDs without an `[INVALIDATED ...]` marker | Without this, overwriting a deal amount in place erases the history of what changed when, and stale copies of the old value in index files can no longer be detected. |
| `check_instruction_file_edit.py` | ask: Edit/Write on protected instruction files | Shared rules live in one canonical file read by two agents. Writing them back into CLAUDE.md would revive double maintenance. |
| `check_agent_fanout.py` | note on 1st research agent, warning from the 2nd agent | Many parallel subagents multiply cost and contend for the same resources, so the check warns from the second one. |
| `check_web_route.py` | deny: WebSearch on providers without it. warn: WebFetch on a search engine | Built-in web search is unavailable on some model providers, so search goes to a local SearXNG instance. WebFetch against search engines returns CAPTCHAs. |
| `check_context_size.py` (PostToolUse) | note at 200k / 300k / 400k context tokens | In a usage audit, requests that re-wrote over 100k tokens of prompt cache were about 5% of requests and about 35% of cost. The cost per cache expiry scales with the context size, so that is what gets watched. |
| `pretool_hooks.py` inline | deny: Write/Edit directly under `~/Documents` (configurable root and allowed subfolders) | Keeps generated files out of the root folder. |

## Design

**One process, many checks.** A hook's cost is dominated by how many processes it
starts. Measured as separate scripts, four Write hooks cost about 1.5 s per Write and the
Bash hooks started about 20 processes, about 5 s per call. `pretool_hooks.py` is registered once and
runs each check in-process with `runpy`, swapping stdin/stdout and catching `SystemExit`.
The check scripts are unchanged by this: each still runs standalone with the same output.

**Strongest verdict wins.** deny > ask > allow. One deny from any check is a deny.

**Fail-open or fail-closed is a per-check decision.** Claude Code itself fails open: a hook
that crashes or times out lets the call through. The dispatcher makes the choice explicit:

- `open`: a crashing check is skipped and reported on stderr. Used where a bug would
  otherwise brick normal work (every Bash call, every file edit).
- `closed`: a crashing check becomes a deny. Used where the fallback is cheap (chat send;
  drafting still works).

A crash in one check never removes another check's deny.

**Every block offers a way forward.** A deny message says what to do instead (write a
script file, use the Grep tool, create a draft). A guard that blocks without an
alternative teaches the agent to evade it.

**Every deny names its rule** (`[rule=...]`), so regression reports group by rule.

## Install

1. Copy `hooks/` somewhere stable.
2. Merge `settings.example.json` into `~/.claude/settings.json` and replace
   `/path/to/guardrails` with the real path.
3. Adjust `hooks/guard_config.py` or the `GUARD_*` environment variables:
   protected root, memory directories, chat tool names, research agent names,
   irreversible-action patterns, segment rules.
4. Run the self-test:

```bash
python hooks/pretool_hooks.py --selftest
python hooks/check_context_size.py --selftest
```

Requires Python 3.8+ and nothing outside the standard library.

## Regression against real history

Synthetic tests do not find false positives; real commands are messier than anything you
would invent. `regression/` replays the tool calls from your own Claude Code transcripts
through the hooks. Details and the false-positive patterns found so far are in
`regression/SKILL.md`.

```bash
python regression/scripts/extract_corpus.py --out /tmp/corpus.jsonl --transcripts --tools Bash --since 30d
python regression/scripts/judge.py --hook hooks/pretool_hooks.py \
  --corpus /tmp/corpus.jsonl --force-tool Bash --report /tmp/report.md
```

Then label every block by hand as a true or false positive. Deploy only with 0 false
positives and 0 hook exceptions. An empty corpus is a failed extraction, not a pass.

## Layout

```
guardrails/
  hooks/
    pretool_hooks.py              dispatcher (PreToolUse)
    guard_config.py               all tunables, env-overridable
    bash_guard.py                 Bash checks
    check_*.py                    one file per check
    memory_values.py              value extraction for the memory check
    hook_codecache.py             compiled-code cache for runpy
  regression/
    SKILL.md
    scripts/extract_corpus.py
    scripts/judge.py
  settings.example.json
```
