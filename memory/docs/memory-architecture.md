# Long-term memory architecture for a coding agent

This is the design I run for Claude Code's file-based auto memory
(`~/.claude/projects/<project-key>/memory/`). Each rule below names the risk it addresses:
the agent acting on a stale amount, an index line that drops the one condition that matters,
or an index that points at files that do not exist.

## 1. Three layers, three jobs

Before writing anything, decide which layer it belongs to.

| Layer | Job | May it be summarized? |
|---|---|---|
| **Handoff** (`handoff/*.txt`) | What happened in this session, how to resume | **Yes.** Read once by the next session, deleted after 14 days |
| **Body** (`project_*.md`, `feedback_*.md`) | Decision + reason + the conditions under which it holds | **No.** Drop the conditions and the decision cannot be reproduced |
| **Index line** (`MEMORY.md`, `index_*.md`) | **When to open** the body | **No.** The agent decides from the index alone, without the conditions |

### Index lines are triggers, not facts

The moment an index line lists facts, the agent stops opening the body. Allowed content:
(1) trigger words, (2) whose move it is and the deadline, (3) the heading of an unresolved item.
**Over 300 bytes, suspect summary creep.**

```
bad : no pre-distribution / 3 printed sets / recording OK only inside the org / survey run by the client / 37 seats fixed
good: Before deciding on-site logistics (handouts, printing, recording, survey channel), open the *260914 section
```

Detection: `tools/memory_index_lint.py` scores each index line on byte length (soft 300 / hard
600), number of enumerations, progress-log phrases ("... done", "... sent") and whether it
points to the body at all.

**No bulk rewrite.** Some information exists only in the index; deleting it loses it. Fix the
lines you touch, when you touch them.

## 2. Invalidate, do not delete

When a value in a body changes, keep the old value and mark it:

```
[invalidated 260928] old $48,000 (Plus bundle) -> new $12,000 (5 Creator seats only)
```

(The original notation is `【無効260928】`; the lint tool accepts both.) If the old value was
simply wrong, mark it as an erratum instead. Deleting loses *when and how* it changed, and an
index line still quoting the old value can no longer be detected.

Two guards back this up:

- A pre-edit hook denies edits that remove an amount, a pipeline stage or a record ID from a
  project/feedback/reference body without an invalidation marker (moves within the same file
  and moves to another memory during GC pass). Over 30 days of edits it fired on **0.9%**
  of edits. (The hook itself is not in this repo.)
- `memory_index_lint.py --drift` finds the three kinds of index/body disagreement:
  - **A** the index says "open the *YYMMDD section", but the body has a newer dated section
  - **B** a value in the index does not exist anywhere in the body
  - **C** a value the body has invalidated is still shown in the index

For whole statements that are superseded (not single values), the same idea applies with an
HTML comment on the line before the old text:

```markdown
<!-- superseded:260827 by:direct CLI path -->
Writes must go through the chat bot relay. The CLI token is revoked.
```

Text right after a `superseded` marker is history, not instruction. If two statements
conflict, follow the one without the marker. The reason to keep the old text: losing "why we
did not choose that path" invites re-inventing it. Example scenario: a tool's official re-auth
command is recorded in memory, but without the history an agent could spend hours rebuilding
a worse version with browser automation.

## 3. Two-tier index under the auto-load budget

Claude Code auto-loads `MEMORY.md` only up to a boundary: the first 200 lines or roughly 25KB
of effective content. Anything after that is invisible. The binding limit in practice is
**bytes, not lines**.

- **`MEMORY.md` = router + HOT.** Kept under a soft limit of 23KB. Contains the always-on rule
  pointer, user facts, active projects, about 15 HOT feedback lines, and one router line per
  sub-index.
- **Sub-indexes `index_*.md` = detailed index per domain.** No byte limit, read on demand.
- Router lines and sub-index headers are written as **when to open** triggers.

### Where new index lines go

- New memories get their index line in a **sub-index** (this overrides the harness default of
  "add a one-line pointer in MEMORY.md").
- `MEMORY.md` itself only gains a line for (a) always-on-level rules, (b) state of an active
  project, (c) proven HOT entries (read 4+ times).
- **An index line is mandatory.** A memory with only a frontmatter description and no index
  line was not auto-recalled in a probe test. Without an index line it is reachable only by grep,
  which in practice means forgotten.

## 4. Verify index and files in both directions

Checking only one direction ("a memory has no index line") misses half the problem.
Comparing the index with the files as two sets once showed:

| Direction of the break | Symptom | First measurement -> after repair (same day) |
|---|---|---|
| **Orphan** (body, no index) | Never auto-recalled; reachable only by grep | 41 -> **0** |
| **Ghost** (index, no body) | "Details in X.md" points to nothing | 119 -> 86 |
| of which **injected every turn** (named by `MEMORY.md` HOT lines, always-on rules, `CLAUDE.md`) | Misfires on every single turn | 28 -> **0** |

Index names: 284. Body files: 206. **Present in both: 165 (51%).**

Lessons:

- **Do not compare counts and call it a match rate.** Compare the name sets.
- **Ghosts differ in weight.** Fix the ones injected every turn first. Do not rush to delete
  ghosts that only appear in sub-indexes: the name is the search key for locating the body.
- **When no copy of the body can be found, build a minimal body from the index lines**
  instead of deleting them. Deleting would throw away the summary the index still held, and the
  stub costs zero bytes of `MEMORY.md`. Mark stubs with `reconstructed:` so that
  "matched 100%" is never read as "full content present".
- A freshness scan answers "is it old?", never "does it exist?". The break stays invisible
  unless you check for it. Run the check after bulk file moves or restores, after GC, and after rewriting the
  sources cited by always-on rules.

Tool: `skill/memory-index-repair/` (`scan.py`, `propose.py`, `filecheck.py`).

## 5. One entity, one file

A project memory is one file per deal / organization / person / initiative. Different
versions, phases or angles of the same entity are sections in that file.

- good: `# ACME City analytics` with sections `## Current status`, `## Data constraints v5`,
  `## Output redesign`, `## Archive (past decisions)`
- bad: `project_acme_dataset.md` + `project_acme_v9.md` + `project_acme_output.md`. Freshness
  cannot be managed, and auto-recall mixes old and new.

A new file is allowed only for a clearly separate engagement (different department, contract
or budget, or a re-proposal in a new fiscal year that should not inherit the old context).

## 6. Promotion, demotion, decay

- **Promotion:** a sub-index entry whose body has been read 4+ times (counted by a
  `PostToolUse` hook on `Read|Grep` into an access ledger) becomes a HOT candidate. Keep HOT at
  about 15 lines; promoting one means demoting the weakest.
- **Do not promote candidates blindly.** The ledger counts are cumulative and include history
  from before a demotion. The top candidate in one GC run (21 reads) was a project that had been
  closed and deliberately demoted five days earlier. The detector does not know why something
  was demoted, so each index line carries `<!-- why:... since:YYMMDD -->`, and you read it
  before promoting.
- **Demotion has two triggers:** (1) low decay score at GC time, (2) **the project is settled**,
  meaning nothing is left on our side and only the other party's move remains. Demote at once,
  at the moment of the decision. Waiting for the next GC keeps a settled project in HOT on
  every turn; demoting one on the day freed 1,947 bytes and brought `MEMORY.md` back under
  the soft limit.
- **Write a reopen trigger when demoting**, not "done". A cold index that does not say what
  should make you reopen the body becomes a graveyard.

```
- [ACME City](project_acme_city.md) - Settled 260819: pilot declined for this fiscal year.
  Reopen when: (1) the client publishes next year's budget (2) the security review answer arrives.
  <!-- why:only client-side moves left since:260819 -->
```

- `why` comments go on index lines, `superseded` comments go in bodies. Both are HTML comments
  and cost nothing against the load budget. Lines whose rationale cannot be read become
  impossible to delete safely, so prefer deleting lines that carry a rationale.
- GC order: move low-frequency lines to sub-indexes, then **rewrite remaining sections as
  "current state"** (wholesale rewrite stops growth better than line-by-line deletion), then
  handle decay candidates with a human gate. Measure effective bytes, not raw `wc -c`.
- Never delete a body file during compression. Only index lines move; bodies stay reachable by
  grep.

## 7. Always-on rules (Layer 0)

A short file of "never violate" rules is injected on every prompt by a `UserPromptSubmit`
hook, composed per working directory (a shared core plus zone-specific additions). Promotion
into it requires a high-cost or irreversible risk, or a rule the agent tends to skip, and it is
capped at about 10 items. Anything that belongs to one zone stays out of the shared core,
otherwise every session pays for it on every turn.
