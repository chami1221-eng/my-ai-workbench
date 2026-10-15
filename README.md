# my-ai-workbench

The parts of my daily AI agent environment that are worth sharing: the guardrails, the memory design, the performance tooling and the mods that keep an AI agent useful and safe in real work.

I am not a software engineer. I sell enterprise software to local governments in Japan, and I run most of my work through Claude Code and Codex: account research, proposal drafts, CRM updates, calendar and email triage, live demos. Over a year that grew into 160+ custom skills, 100+ scripts, 18 guardrail hooks and 550+ structured memory files. I designed the system and its rules; most of the code was written by the agents under my direction, and every rule here was tested on my own work first.

What is in this repo is the general-purpose layer. The business-specific skills stay private.

## Not tied to one tool

I run the same work on Claude Code and on Codex, and I pick whichever fits the task. The code here is written for Claude Code because that is where its hook and plugin APIs let me enforce rules most completely. The ideas carry over to any agent harness:

- Approval gates for outbound sends and irreversible changes
- Secrets masked before they reach the model or the transcript
- Memory that keeps its history and is checked against its own index
- Measuring cost and latency before changing anything

The harnesses differ in where they can enforce a rule. For example, a hook that only sees shell commands cannot stop a file edit made through a separate patch tool, so on that kind of harness the same rule has to live in a different place. Knowing those gaps is part of running agents safely.

## What was removed before publishing

This repo is a sanitized extract, not a copy of my working setup.

- **No customer, employer or personal data.** Customer and organization names, people's names, record IDs, deal values, internal system names, file paths and channel IDs were removed or replaced with generic examples (for example "ACME City").
- **No internal infrastructure details.** Which internal services, vendors or security tools my workplace uses is not described anywhere.
- **No secrets.** Tokens and keys were never part of the source; the code reads them from the environment, and `.env` is ignored.
- **Risks, not stories.** Each guardrail is described by the risk it addresses, written as a general scenario.
- **Business-specific skills are not included.** Only the general-purpose layer is published.
- Every file was checked with a pattern scan for the categories above before publishing.

## What is here

| Folder | What it is | Start with |
|---|---|---|
| [guardrails/](guardrails/) | PreToolUse hooks that enforce what instructions alone cannot guarantee (no outbound sends without approval, no silent config or memory overwrites, no irreversible actions by the agent), plus a regression harness that replays real past tool calls against the hooks | [guardrails/README.md](guardrails/README.md) |
| [mods/](mods/) | Claude Code mods (plugins written as function hooks): an approval gate for outbound messages, secret masking before anything reaches the transcript, a resident hook runner, prompt-cache friendly rule injection, and small UI helpers | [mods/README.md](mods/README.md) |
| [memory/](memory/) | How I keep an agent's long-term memory accurate over months: a 2-tier index under the auto-load budget, "invalidate, don't delete", and checks that the index and the files still match | [memory/README.md](memory/README.md) |
| [perf/](perf/) | `cc-perf-doctor`: measure why Claude Code is slow (hook blocking time, async CPU, prompt-prefix tokens) before changing anything | [perf/README.md](perf/README.md) |

## Principles behind all of it

1. **A rule in the prompt is a wish; a hook is a guarantee.** Every guardrail here began as a sentence in an instruction file. An instruction depends on the model remembering it; a hook runs before the tool call whether the model remembers or not. Anything that would be costly or irreversible if missed gets enforced in code.
2. **A human approves what leaves the machine.** The agent may draft anything. Sending, posting or making an irreversible change needs the user to have seen the full text and said yes in the conversation. A permission dialog that does not show the body is not an approval.
3. **Secrets never enter the conversation.** Masking happens before a row is stored, so neither the model nor the transcript sees the value.
4. **Measure before touching.** Performance fixes and rule changes are judged by before/after numbers on the same machine, not by feel.
5. **Memory keeps its history.** When a fact changes, the old value is marked invalid instead of deleted, so "what changed when" survives and stale copies can still be found.

## Status

The guardrails, the memory tools, `cc-perf-doctor` and four of the mods (`secret-mask`, `hook-daemon`, `layer0-compose`, `shot-peek`) run in my daily environment. `send-gate`, `bash-rewrite` and `prompt-band` are experimental. Paths, tool names and patterns are configurable; defaults are examples, so read each README before wiring anything in.

Tested on Windows 11 with Claude Code and Python 3.13. Mod tests run with `claude plugin test`; Python checks have `--selftest` modes.

## Related

- [tableau-twb-xml-toolkit](https://github.com/chami1221-eng/tableau-twb-xml-toolkit): agent skills and schemas for generating and editing Tableau workbooks directly with LLM agents.

## License

Apache License 2.0. See [LICENSE](LICENSE).
