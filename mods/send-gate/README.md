# send-gate

## What it does

Before any call that drafts, schedules or sends an outbound message, checks two things against the conversation:

1. The full body was shown in the conversation.
2. The user said something after it was shown.

If either is missing, the call is denied with a message telling the agent to show the full text and wait.

## Why / risk it addresses

Risk: a permission dialog that does not show the message body is not a real approval of the text. A sending script without a hook depends on the agent remembering the rule "show the text, get an OK, then draft".

Replaying 14 days of sessions: 63 of 71 chat drafts would pass. The 8 that would be stopped were drafted before the text was shown, or had sentences added after it was shown.

## How it works

- Coverage instead of equality: the body and the conversation are normalised (Markdown, quote marks, bullets, whitespace removed). The score is the share of body characters found as runs of 8 or more consecutive characters. A one-word edit does not fail a whole line. Threshold: 90%.
- The shown text plus the user's later messages count together, so "change X to Y, then draft it" passes when the new wording is in the user's message.
- A body the user wrote themselves passes.
- Tool results, system injections (`<...>`) and interruption markers do not count as a user reply.
- Only the last 80 messages are searched, so an old draft cannot approve a new one.
- Chat mention markup (`<@U...>`) is ignored in the comparison.
- Bash senders: the body is read from an inline flag or from a body file before the command runs. Dry runs (no commit flag) are ignored. If the body file cannot be read, the call is denied with instructions to create the file first in a separate step.
- `/send-gate` shows allowed and stopped counts.

## Config

- `GATED_TOOLS` in `hooks/register.ts`: tool name to `{ label, bodyField }`. Defaults are the draft, schedule and send tools of a chat MCP server (for example Slack).
- `BASH_SENDERS` in `hooks/gate.ts`: `{ kind, match: RegExp[], bodyFlag?, bodyFileFlag? }`. A Bash command is gated when every regex matches. The defaults (`email-draft.py --commit`, `post-message.py post --confirm`) are placeholders for your own scripts.
- `THRESHOLD`, `K`, `WINDOW` at the top of `hooks/gate.ts`.

One unfiltered `tool.call` handler is used so the tool list can live in config. Tools not in the list pass straight through.

## Files

- `hooks/gate.ts` (pure functions), `hooks/register.ts`, `hooks/register.test.ts`, `hooks/hooks.json`, `.claude-plugin/plugin.json`
- `tsconfig.json` extends `./.claude-plugin/types/tsconfig.json`, which comes from the Claude Code plugin SDK and is not included here.

Run tests: `claude plugin test <path-to-this-folder>`

## Status

Experimental. Not yet enabled in my daily setup.
