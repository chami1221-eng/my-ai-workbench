# shot-peek

## What it does

When Claude reads an image from the Screenshots folder, a line above the prompt shows which screenshots it read, with the capture time taken from the file name (for example `10/03 12:50:49`). The line clears on the next submit and has a Close button.

## Why / risk it addresses

I use a one-key shortcut that tells Claude to read my latest screenshot (or the latest N). Risk: it can pick a different file than the one I meant, and a confident answer about the wrong image is easy to take at face value. This shows the choice at a glance.

## How it works

- `tool.call` on `Read`: after the read succeeds, if the path matches the Screenshots pattern, the file is added to a short list (last 3). Read itself is never changed.
- `ui.render` on `AbovePrompt`: renders the list.
- `prompt.submit`: clears it.

A thumbnail would be nicer, but the Image element did not render in Windows Terminal, so only the time is shown.

## Config

In `hooks/register.tsx`:

- `SHOT`: regex for which paths count as screenshots. Default: any `.png` under `Pictures/Screenshots` (the Windows default folder).
- `MAX`: how many to list.

## Files

- `hooks/register.tsx`, `hooks/register.test.ts`, `hooks/hooks.json`, `.claude-plugin/plugin.json`
- `types/index.d.ts`: this mod's own `$.state` declaration
- `tsconfig.json` extends `./.claude-plugin/types/tsconfig.json`, which comes from the Claude Code plugin SDK and is not included here.

Run tests: `claude plugin test <path-to-this-folder>`

## Status

In daily use.
