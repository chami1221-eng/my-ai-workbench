# prompt-band

## What it does

A band above the prompt that shows up to three kinds of rows, only when there is something to show:

- `⏳ 0:42 Bash <description>`: tools that have been running for 8 s or more, with elapsed time. Subagent tools are marked with `↳`.
- `c approves: <question>`: when the last reply ends with a question or a request for approval, the line that a one-key "c" reply would approve.
- `ctx used 63%, suggestion: ...`: a wrap-up hint at 40 / 60 / 75% context usage, with a Close button.

## Why / risk it addresses

- While a tool is busy, elapsed time answers "is it still running?" without sending a message or interrupting.
- A one-key "continue" that doubles as approval can approve something other than what you think. Showing the pending question first removes that risk.
- I ran a separate "should I wrap up now?" check 59 times. The band shows the same thresholds passively.

## How it works

- `tool.call` (all tools): records start time and a label (description, command, prompt, path or pattern), removes it when the tool finishes.
- `clock.every(1000)`: re-renders only while a slow tool is running.
- `turn.complete`: scans the last 4 lines of the reply for a question (Japanese and English patterns), and reads context usage.
- `ui.render` on `AbovePrompt`: our rows are stacked on top of `next(e)`, so other mods' bands (for example shot-peek) still show.

## Config

At the top of `hooks/register.tsx`: `SLOW_MS`, `MAX_ROWS`, `APPROVE_KEY`, `ASK_RE` (question patterns), and `levelOf` (thresholds and hint text).

## Files

- `hooks/register.tsx`, `hooks/register.test.tsx`, `hooks/hooks.json`, `.claude-plugin/plugin.json`
- `types/index.d.ts`: this mod's own `$.state` declaration
- `tsconfig.json` extends `./.claude-plugin/types/tsconfig.json`, which comes from the Claude Code plugin SDK and is not included here.

Run tests: `claude plugin test <path-to-this-folder>`

## Status

Experimental. Not yet enabled in my daily setup.
