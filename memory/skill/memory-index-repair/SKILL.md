---
name: memory-index-repair
description: >
  Detect and repair breaks between the memory index (MEMORY.md / index_*.md) and the memory
  body files, in both directions: orphans (body exists, no index line, so it is never
  auto-recalled) and ghost links (index line exists, body does not, so the pointer leads nowhere).
  Also checks that file paths named inside memories actually exist.
  Use whenever: right after moving or restoring many memories / right after a memory GC /
  after rewriting the sources cited by always-on rules / when you suspect a memory is unreachable.
  Triggers: "index check", "memory-index-repair", "ghost links", "orphan memories".
  Do NOT use: freshness or decay scoring, MEMORY.md byte-budget trimming, writing new memories.
---

# memory-index-repair: two-way check of index and bodies

## Why / risk it addresses

A one-way check misses half the problem. A rule like "a memory without an index line is never
auto-recalled" only covers orphans, and a freshness scan only asks "is it old?". Neither asks
"does the file the index points to exist?".

Comparing the index with the files as two sets once showed:

| | First measurement | After repair (same day) |
|---|---:|---:|
| Names in the index | 284 | 321 |
| Body files | 206 | 235 |
| **Present in both** | **165 (51%)** | **235 (73%)** |
| Ghost links (index, no body) | 119 | 86 |
| of which **injected every turn** | **28** | **0** |
| Orphans (body, no index) | 41 | **0** |

Risk in each direction: an always-on rules file can tell the agent on every turn "details in
`feedback_x.md`" while that body does not exist; and the source of a critical "draft, confirm,
then send" rule can exist as a file while no index points to it.

> Do not compare counts and call it "match rate X%". Compare the two name sets.

### "Matched 100%" is not "done"

`matched` only counts whether a body file exists, not whether it has real content.
Stubs rebuilt from index lines (Step 1.5) also count as matched. `scan.py` therefore always
prints the stub count and the number of bodies with real content:

```
  bodies 325 / indexed names 325 / matched 325 (100%)
    of which stubs (rebuilt from index lines, content not recovered): 114
    -> bodies with real content: 211 (65%)
```

After stubbing every ghost, a report can show 0 ghosts and 0 orphans (100%) while a third of
the bodies are stubs. Without that line the report reads as finished. Stub detection uses the frontmatter key `reconstructed:`; removing the
banner when the content is restored makes the number go down on its own.

### Fix "injected every turn but points nowhere" first

Ghosts named by `MEMORY.md` itself (HOT lines), `layer0.md`, `CLAUDE.md` or always-on rules
files misfire on every turn. `scan.py` lists those first. Fixing only those removes most of
the practical impact; the rest only appear in sub-indexes and are hit only when opened.

**Do not rush to delete ghosts that only appear in sub-indexes.** The name is the search key
for locating the body.

## Usage

```bash
# detect (default memory dir = $CLAUDE_MEMORY_DIR or ~/.claude/projects/<cwd-key>/memory)
python scripts/scan.py
python scripts/scan.py --memory-dir path/to/memory --cite-file path/to/CLAUDE.md --cite-dir path/to/rules
python scripts/scan.py --format json

# material for writing index lines for orphans (frontmatter description + suggested sub-index)
python scripts/propose.py --memory-dir path/to/memory

# do the files named inside memories exist?
python scripts/filecheck.py --memory-dir path/to/memory --search-root path/to/shared/drive
```

An optional `nonghost.txt` next to this file lists names that appear but correctly have no
body (for example "bad example" file names quoted in a rules document).

---

## The other break: do files named in memory exist? (`filecheck.py`)

`scan.py` checks **index <-> memory body (.md)**. `filecheck.py` checks
**memory body -> deliverables, scripts, documents**.

Verdicts: `ok` / **`GHOST`** (named but missing) / **`moved`** (same file name exists elsewhere
under `--search-root`, so the path in memory is stale) / `skip` (placeholders).

Risk it addresses: a project memory or a handoff can say a customer deliverable was "created
and placed in the shared drive" when it exists nowhere. Each later session trusts the note, so
the gap stays hidden until someone needs the file.

First measurement on a real memory set: 677 paths extracted / 451 ok / **32 GHOST** /
**5 moved** / 189 skip.

> Diagnosis order: a False from `exists()` on a synced drive can look like the drive "lying",
> when the file was simply moved one folder down (at the new path `exists()` is True and the
> size matches). **When `exists()` says False, suspect "moved or deleted" before suspecting the
> measuring instrument.** (The opposite risk, trusting a broken instrument, is also real.
> Check both directions.)

Handling results:

- **moved**: usually fix the path in the memory.
- **GHOST**: classify. Deliverables (pptx / xlsx / pdf) matter most. Scripts and caches are
  often stale references. Temp files are expected to disappear; mark them as temporary.
- When to run: after a day that produced many deliverables / right after picking up a handoff /
  when "where is that file?" fails.

---

## Repair procedure

### Step 1: triage ghosts, highest impact first

For each ghost, in this order:

1. **Look for the body** (file history, transcripts, backups). If found, restore it and stop.
   Do not touch the index line.
2. **If the content was absorbed into another memory, repoint the index line to it.**
   Deleting the line means nobody can reach it with that word again.
3. **Only if neither works, delete the index line.** Last resort. Also fix every place that used
   the name as a trigger (for example a rule that cites it as its source).

### Step 1.5: if no body can be found, turn the index lines into a minimal body

When no copy of the body can be found anywhere, deleting the index line would also throw away the summary the index still holds. Instead, collect every line in
`MEMORY.md` / `index_*.md` / `layer0.md` / `CLAUDE.md` / rules that names it and write them
into a minimal body. **Add zero new information.**

- It adds zero bytes to `MEMORY.md`, so it is cheaper than annotating the index line.
- The stub carries a "this is not a restoration" banner and `reconstructed:` in frontmatter.
  Remove the banner when the real content is back. The banner marks the remaining holes.
- A project stub acts as the container to refill later from notes and the CRM.

(The stub generator itself is not included in this repo; it is a short script over the same
citation sources `scan.py` reads.)

### Step 2: add index lines for orphans

`propose.py` puts the frontmatter description and a suggested sub-index on one screen.

- Write the index line as a **WHEN-to-open trigger**, not a summary.
- Write it in a **sub-index**. Only add to `MEMORY.md` itself for (a) always-on-level rules,
  (b) state updates of active projects, (c) proven HOT entries (read 4+ times).
- Routing is a keyword suggestion. The final decision is yours.
- Do not do everything at once. Work one sub-index at a time.

### Step 3: measure again

```bash
python scripts/scan.py
```

Confirm the orphan and ghost counts actually went down. Do not finish on "I think I fixed it".

---

## Pitfalls

- **`feedback_xxx.md` strings appear in body text all the time.** `scan.py` reads only index
  files as index sources. Cross-links between bodies are not index lines; counting them would
  hide orphans.
- **`layer0.md` is not an index** and is excluded as an index source, but it is read as a
  citation source (to rank ghost impact).
- **Zero orphans is not the goal.** If a memory is truly unneeded, delete the memory instead of
  indexing it.
- **Never bulk-delete ghost links.** A surviving name proves the knowledge existed and is the
  search key for finding it: file history searched by index name is the most direct way back
  to a body.
