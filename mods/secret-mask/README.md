# secret-mask

## What it does

Masks tokens, API keys, passwords and private keys in every row that enters the Claude Code conversation (tool results, hook context, user input, assistant replies) before the row is stored. The model and the transcript file only ever see `[REDACTED:<kind>]`.

## Why / risk it addresses

Risk: tokens printed by a command end up in the transcript and model context. A written rule ("never print the output of a command that returns a token") depends on attention, so the check lives in the pipeline instead.

## How it works

- `session.append` runs once per row right before it is stored. The mod rewrites `text` blocks and `tool_result` contents and leaves everything else (images, `tool_use`) untouched.
- Two rule sets:
  - Shape rules: values recognisable on their own (private key blocks, chat bot tokens, OAuth access/refresh tokens, cloud API keys, GitHub tokens, JWTs, `Bearer ...`).
  - Key-name rules: `SOMETHING_TOKEN=...`, `"password": "..."` and similar. Only the value is masked, and only if it is long enough and has both letters and digits, so `token: 5000` or a UUID session ID is left alone.
- Colored CLI output can split a token with ANSI escape codes. If nothing matches, the mod strips colors and tries again.
- `session.append` only changes what the model reads. The raw Bash result record in the transcript would still keep the original, so the mod also hooks `tool.call` for Bash and masks `stdout` / `stderr` in the result itself.
- If masking throws, the row passes unchanged. The conversation is never blocked.
- `/secret-mask` shows how many rows and values were masked, by kind.

Limits: the terminal may briefly show the unmasked text before it is stored. This is a safety net, not permission to print secrets.

## Config

Edit the `RULES` and `KV_RULES` arrays at the top of `hooks/register.ts`. Each rule has a `kind` (shown in the mask) and a regex. Tests build fake secrets at runtime so the source never contains a realistic one.

## Files

- `hooks/register.ts`, `hooks/register.test.ts`, `hooks/hooks.json`, `.claude-plugin/plugin.json`
- `tsconfig.json` extends `./.claude-plugin/types/tsconfig.json`. That directory holds the Claude Code plugin SDK type definitions and is not included here; it comes from the SDK.

Run tests: `claude plugin test <path-to-this-folder>`

## Status

In daily use.
