# hook-daemon

## What it does

Answers heavy Python command hooks (PreToolUse, PostToolUse, Stop, UserPromptSubmit) from one long-running Python process instead of starting Python on every tool call. It also exposes a read-only `run` tool that executes allowlisted scripts inside that same process.

## Why / risk it addresses

My settings run a Python "dispatcher" hook on most tool calls. Process starts add up: 0.2 to 0.8 s per hook, and about 2,800 hook runs over 20 sessions. With one resident process the start cost is paid once per session.

The `run` tool targets the same cost for read-only lookups. In my notes, a typical read query took about 5.7 s through a script and about 0.3 s from the warm resident process; counting the Bash round trip, the old path was around 20 s.

## How it works

- On `session.start` the mod spawns the server script. Its first stdout line is `{"port": ..., "token": ..., "pid": ...}`. The server listens on 127.0.0.1 only and rejects requests without the per-start random token.
- For each classic hook event the mod checks the hook layout in settings. It only answers when the sync hooks for that event and tool are exactly one dispatcher script. If you add another hook, it steps aside. Async hooks in the same group are re-fired after answering.
- The server runs the unchanged dispatcher script with the same stdin JSON and returns `{code, stdout, stderr, ms}`. The mod reads the reply with Claude Code's own command hook rules (exit 2 = deny/block, JSON `hookSpecificOutput`, plain text as context for UserPromptSubmit).
- Fail-safe: on timeout, transport error, crash (exit code other than 0 or 2) or an unexpected layout, the mod calls `next(e)`. That runs the normal settings hook, so a guard is never skipped. After one transport failure the session stays on the normal path.
- Bash is never intercepted. Answering Bash from `tool.call` would bypass PreToolUse hooks and permission checks, so read-only work goes through a separate tool that gets the normal permission flow.
- While `run` is executing, the single-threaded server is busy. Hooks skip it and use the normal path instead of queuing (queuing can hit the host's 30 s fetch limit, which would disable the fast path for the rest of the session).
- Works with `layer0-compose`: this mod sits outside it and answers UserPromptSubmit itself, so it reads what layer0-compose published in `$.state` and strips the duplicate rule text from the injection on an exact match.
- `/hook-daemon` shows answered vs fallback counts, average latency, and tool stats.

Known limit: a child started with `void $.process.run(...)` from inside a hook is stopped when the hook returns. Async hooks that must survive (for example desktop notifications) should be started by the server or moved out of the mod.

## Server protocol (not included)

The Python server is a separate script. Any implementation that follows this works:

- `POST /` with header `X-Token`, body `{"event": "PreToolUse" | "PostToolUse" | "Stop" | "UserPromptSubmit", "payload": {...}, "env": {...}}` returns `{"code", "stdout", "stderr", "ms"}`
- `{"event": "async", "script": "<name>", ...}` runs an allowlisted async hook
- `{"event": "tool", "payload": {"op": "script", "name": ..., "argv": [...]}}` runs an allowlisted read-only script. The server must enforce the allowlist and reject write subcommands; the plugin only advertises the list.
- `GET /` returns 200 when idle

## Config

| Setting | Where | Default |
|---|---|---|
| Server script | env `HOOK_DAEMON_SCRIPT` | `~/.claude/scripts/hook_daemon.py` |
| Python executable | env `HOOK_DAEMON_PYTHON` | `python` |
| Scripts advertised by `run` | env `HOOK_DAEMON_TOOL_SCRIPTS` (comma separated) | `web-search.py, calendar-read.py, notes-read.py` (examples) |
| Dispatcher script per event | `DISPATCHER_FILE` in `hooks/register.ts` | `pretool-hooks.py`, `posttool-hooks.py`, `stop-hooks.py`, `prompt-hooks.py` |
| Async hooks the server may run | `ASYNC_KNOWN` in `hooks/register.ts` | `memory-access-logger.py` |

## Files

- `hooks/register.ts`, `hooks/register.test.ts`, `hooks/hooks.json`, `.claude-plugin/plugin.json`
- `tsconfig.json` extends `./.claude-plugin/types/tsconfig.json`, which comes from the Claude Code plugin SDK and is not included here.

Run tests: `claude plugin test <path-to-this-folder>`

## Status

In daily use. The public version replaces a work-specific read backend with the generic script allowlist above.
