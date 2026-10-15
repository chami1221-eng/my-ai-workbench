# bash-rewrite

## What it does

Rewrites Bash commands that my PreToolUse guard would deny into an equivalent form, before the guard sees them:

- A heredoc or `python -c` with backslashes in it: the body is written to a file and the command reads that file instead (`python - < file`, `python file.py`).
- `cd somewhere && grep ... a.md`: bare relative file names passed to read commands (grep, cat, head, sed, awk, ...) become absolute paths. The `cd` stays.

## Why / risk it addresses

On Git Bash for Windows, backslashes inside heredocs and `python -c` get mangled, and `cd` + relative paths hide which file is really read. My guard denies both. Over 14 days it denied 256 commands (186 backslash cases, about 70 cd + relative path), and each deny costs a full model round trip just to retype the command.

## How it works

- `tool.call` on Bash. The mod never answers Bash itself; it always calls `next()` with the rewritten command, so PreToolUse hooks and permission checks still run on what actually executes.
- Rewrites only forms that are certainly equivalent. If anything is doubtful (unquoted heredoc with `$`, concatenated quotes, a `cd` target that is a variable, a directory with special characters, `sed -i`), it returns null and the original passes; the guard then stops it as before.
- Inside double quotes, only bash-only escapes (`\"`, `\$`, `` \` ``, line continuation) are removed from `python -c` code; the rest is kept as the model wrote it.
- After running, a note is added to the tool result telling the model what was rewritten and the exact command that ran.
- If the body file cannot be written, the original command passes unchanged.
- `/bash-rewrite` shows counts by kind.

## Config

| Setting | Where | Default |
|---|---|---|
| Where bodies are written | env `BASH_REWRITE_SPILL_DIR` | `<home>/.claude/bash-rewrite` |

Home comes from `USERPROFILE` or `HOME`. Without either, nothing is rewritten. Paths are handled in Git Bash POSIX form (`/c/...`); built and tested on Windows.

## Files

- `hooks/rewrite.ts` (pure functions), `hooks/register.ts`, `hooks/register.test.ts`, `hooks/hooks.json`, `.claude-plugin/plugin.json`
- `tsconfig.json` extends `./.claude-plugin/types/tsconfig.json`, which comes from the Claude Code plugin SDK and is not included here.

Run tests: `claude plugin test <path-to-this-folder>`

## Status

Experimental. Not yet enabled in my daily setup.
