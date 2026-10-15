# layer0-compose

## What it does

Moves my top-priority rule list ("Layer 0", one Markdown file) from per-turn prompt injection into the system prompt. When the system prompt really carries the text, the mod removes the same text from the per-turn injection.

## Why / risk it addresses

A UserPromptSubmit hook re-sends the full rule list on every turn. Measured over 14 days and 198 sessions, that was about 11.6% of all input tokens. In the system prompt the text is cached, and the share measured about 1%.

## How it works

- `prompt.compose`: reads the rule file and appends it as a session-scoped system prompt section.
- `classic.UserPromptSubmit`: removes the injected chunk only if it matches the composed text exactly. No match, unreadable file, or a changed file means nothing is removed. The worst case is the rules appear twice, never zero times.
- Hot reload: when the mod loads mid-session, the added section does not reach the main conversation. The mod only strips when it was loaded within 60 s of session start.
- Plugin order: plugins wrap each other in enable order. `hook-daemon` sits outside and answers UserPromptSubmit itself, so on its own this mod's hook would never run and the rules would be sent twice. The design: this mod publishes the composed text and the startup flag in `$.state`, and `hook-daemon` reads them and strips. Lesson: if you need to change another mod's result, do not rely on order; let the owner publish state and let the answering mod act on it.
- `/layer0-compose` shows how often the text was composed and stripped.

Your settings hooks and prompt hook are not modified. Remove the mod and you are back to per-turn injection.

## Config

| Setting | Where | Default |
|---|---|---|
| Rule file | env `LAYER0_FILE` | `~/.claude/layer0.md` |
| Extra rule file for one project | env `LAYER0_ZONE_FILE` + `LAYER0_ZONE_MATCH` (substring of cwd) | off |
| Heading your prompt hook prints | `HOOK_PREFIX` in `hooks/register.ts` (keep equal to `L0_HOOK_PREFIX` in hook-daemon) | `⚠️ Layer 0 (must not violate, check every turn):` |

## Files

- `hooks/register.ts`, `hooks/register.test.ts`, `hooks/hooks.json`, `.claude-plugin/plugin.json`
- `types/index.d.ts`: this mod's own `$.state` declaration (read by hook-daemon)
- `tsconfig.json` extends `./.claude-plugin/types/tsconfig.json`, which comes from the Claude Code plugin SDK and is not included here.

Run tests: `claude plugin test <path-to-this-folder>`

## Status

In daily use.
