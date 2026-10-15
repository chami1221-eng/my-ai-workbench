import type { Register } from 'claude-code'

import { rewriteCdRelative, rewriteHeredocs, rewritePyInline, toPosix } from './rewrite'
import type { Rewrite } from './rewrite'

// Of the Bash commands a PreToolUse guard denies, rewrite the ones that can be fixed without changing meaning.
// Measured over 14 days: 256 denies (186 backslashes in heredoc / python -c, about 70 cd + relative path),
// each one a wasted model round trip.
//
// Never answer Bash ourselves. Always pass to next(), so PreToolUse hooks and permission checks still run.
// Forms that could not be rewritten pass unchanged and the guard stops them as before.

// ---- Config ------------------------------------------------------------------
// Where heredoc / python -c bodies are written. Override with env BASH_REWRITE_SPILL_DIR.
// Default: <home>/.claude/bash-rewrite (POSIX form, e.g. /c/Users/<you>/.claude/bash-rewrite on Git Bash).
const DEFAULT_SPILL_SUBDIR = '.claude/bash-rewrite'
// ------------------------------------------------------------------------------

type Stats = Record<string, number>

function stamp(ms: number): string {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getUTCFullYear()}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}_${p(d.getUTCHours())}${p(d.getUTCMinutes())}${p(d.getUTCSeconds())}`
}

const toWin = (posix: string) => posix.replace(/^\/([a-z])\//, (_, d: string) => `${d.toUpperCase()}:/`)

// $.env.get needs a literal name (the SDK lists which variables a module reads), so each call is spelled out
const str = (v: unknown) => (typeof v === 'string' && v ? v : undefined)

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'bash-rewrite', description: 'How many commands bash-rewrite rewrote (by kind, cumulative)' })
    return next(e)
  })

  on('command.run', { command: 'bash-rewrite' }, async $ => {
    const s = ((await $.store.get('stats')) as Stats | undefined) ?? {}
    const rows = Object.entries(s).map(([k, v]) => `- ${k}: ${v}`)
    return { text: rows.length ? `bash-rewrite rewrites so far\n${rows.join('\n')}` : 'Nothing rewritten yet' }
  })

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const original = e.command
    if (!original.includes('\\') && !/(?:^|[;&|]|\s)(?:cd|pushd)\s/.test(original)) return next(e)

    const profile =
      str(await $.env.get('USERPROFILE').catch(() => undefined)) ?? str(await $.env.get('HOME').catch(() => undefined))
    const home = profile ? toPosix(profile, '') : null
    if (!home) return next(e) // no home directory known: do not rewrite (the guard decides)
    const spillSetting = str(await $.env.get('BASH_REWRITE_SPILL_DIR').catch(() => undefined))
    const spillDir = toPosix(spillSetting ?? `${home}/${DEFAULT_SPILL_SUBDIR}`, home)
    if (!spillDir) return next(e)
    const base = `${spillDir.replace(/\/+$/, '')}/${stamp(await $.clock.now())}_${Math.random().toString(36).slice(2, 6)}`

    let command = original
    const applied: Rewrite[] = []
    const h = rewriteHeredocs(command, i => `${base}_h${i}.in`)
    if (h) {
      command = h.command
      applied.push(h)
    }
    const py = rewritePyInline(command, i => `${base}_c${i}.py`)
    if (py) {
      command = py.command
      applied.push(py)
    }
    const cd = rewriteCdRelative(command, await $.session.cwd(), home)
    if (cd) {
      command = cd.command
      applied.push(cd)
    }
    if (!applied.length) return next(e)

    const spills = applied.flatMap(a => a.spills)
    try {
      for (const s of spills) await $.fs.write(toWin(s.name), s.text)
    } catch (err) {
      $.ui.log(`bash-rewrite: write failed ${String(err).slice(0, 200)}`, { to: 'debug' })
      return next(e) // cannot write: pass the original (the guard stops it)
    }

    const kinds = applied.flatMap(a => a.kinds)
    const stats = ((await $.store.get('stats')) as Stats | undefined) ?? {}
    for (const k of kinds) stats[k] = (stats[k] ?? 0) + 1
    await $.store.set('stats', stats)

    const ran = await next({ ...e, command })
    if (ran.deny !== undefined) return ran

    const notes: string[] = []
    if (spills.length)
      notes.push(
        `wrote the heredoc / python -c body to ${spills.map(s => s.name).join(', ')} and ran it (backslashes arrived exactly as written).`,
      )
    if (cd) notes.push('turned relative file names after cd into absolute paths.')
    $.ui.toast(`bash-rewrite: rewrote ${[...new Set(kinds)].join(', ')} and ran it`)
    return {
      ...ran,
      context: [...(ran.context ?? []), `bash-rewrite: ${notes.join(' ')} Command that ran:\n${command}`],
    }
  })
}
