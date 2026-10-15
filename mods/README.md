# Claude Code mods

Plugins written as function hooks. Each folder has its own README (what / why / how / config / status) and tests (`claude plugin test <folder>`).

| Mod | What it does | Status |
|---|---|---|
| [send-gate](send-gate/) | Before any draft, scheduled or immediate outbound message, checks that the full body was shown in the conversation and the user approved it. Otherwise blocks the call | experimental |
| [secret-mask](secret-mask/) | Replaces tokens, API keys, passwords and private keys with `[REDACTED:kind]` in every row before it is stored, so the model and the transcript never see them | daily use |
| [hook-daemon](hook-daemon/) | Answers heavy Python hooks from one resident process instead of starting Python on every tool call; also exposes a read-only `run` tool for allowlisted scripts | daily use |
| [layer0-compose](layer0-compose/) | Moves the top-priority rule list from per-turn injection into the system prompt, where prompt caching applies | daily use |
| [bash-rewrite](bash-rewrite/) | Rewrites Bash commands the guard would deny into an equivalent allowed form, so the agent does not lose a turn | experimental |
| [prompt-band](prompt-band/) | A band above the prompt: long-running tools, what a one-key approval would approve, and a context-usage hint | experimental |
| [shot-peek](shot-peek/) | Shows which screenshot files the agent just read, so a wrong-screenshot read is visible at a glance | daily use |

The `types/` definitions from the Claude Code plugin SDK are not included; each `tsconfig.json` expects them from your local plugin setup.
