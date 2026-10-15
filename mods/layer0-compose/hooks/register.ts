import type { Register } from 'claude-code'

// Put the Layer 0 rules into the session section of the system prompt, and remove the same text from the
// per-turn UserPromptSubmit injection done by your own prompt hook.
//
// Safety: text is removed only when it exactly matches what this session placed in the system prompt.
//   If the file is unreadable, changed mid-session, or does not match, nothing is removed (it appears twice,
//   never zero times). settings.json and your prompt hook are not touched. Remove the mod and you are back to
//   per-turn injection.

// ---- Config ------------------------------------------------------------------
// Main rule file. Override with env LAYER0_FILE. "~/" is expanded from HOME / USERPROFILE.
const DEFAULT_LAYER0_FILE = '~/.claude/layer0.md'
// Optional extra rule file appended when the session cwd contains LAYER0_ZONE_MATCH
// (e.g. a stricter rule set for one project). Both env vars must be set to enable it.
//   LAYER0_ZONE_FILE  = path of the extra file
//   LAYER0_ZONE_MATCH = substring of cwd that turns it on
// Heading your prompt hook prints before the Layer 0 text (must match the hook, and hook-daemon's L0_HOOK_PREFIX).
export const HOOK_PREFIX = '⚠️ Layer 0 (must not violate, check every turn):\n'
const SECTION_HEAD =
  '# Layer 0 (must not violate)\n' +
  'These are the highest-priority rules in this environment, each guarding against a costly or irreversible failure. ' +
  'Before calling any tool, check that you are not breaking one of them.\n\n'
// ------------------------------------------------------------------------------

let composed: string | null = null // the Layer 0 text placed in the system prompt (used by the hook side)
// Measured: when loaded by a mid-session hot reload, the added section does not reach the main conversation
//   (compose is called but the text does not enter the system prompt). Only strip when loaded at startup.
//   If loaded mid-session, the section is still added but nothing is stripped (twice, never zero).
export const STARTUP_WINDOW_MS = 60_000
let startupLoad = false
const stats = { composed: 0, stripped: 0, missed: 0 }

export function isStartupLoad(loadedAt: number, startedAt: number): boolean {
  return Number.isFinite(startedAt) && loadedAt - startedAt >= 0 && loadedAt - startedAt < STARTUP_WINDOW_MS
}

// The Layer 0 chunk the prompt hook puts into additionalContext (stripped, joined with '\n\n')
export function hookChunk(layer0: string): string {
  return (HOOK_PREFIX + layer0).trim()
}

// Remove the Layer 0 chunk from an additionalContext array. null if nothing matched.
export function strip(ctx: readonly string[], layer0: string): string[] | null {
  const chunk = hookChunk(layer0)
  let hit = false
  const out: string[] = []
  for (const s of ctx) {
    let t = s
    if (t === chunk) { hit = true; continue }
    if (t.startsWith(chunk + '\n\n')) { t = t.slice(chunk.length + 2); hit = true }
    else if (t.includes('\n\n' + chunk)) { t = t.replace('\n\n' + chunk, ''); hit = true }
    if (t.trim()) out.push(t)
  }
  return hit ? out : null
}

export function expandHome(p: string, home: string): string {
  if (p === '~') return home
  if (p.startsWith('~/') || p.startsWith('~\\')) return home.replace(/[\\/]+$/, '') + '/' + p.slice(2)
  return p
}

// $.env.get needs a literal name (the SDK lists which variables a module reads), so each call is spelled out
const str = (v: unknown) => (typeof v === 'string' && v ? v : undefined)

async function readLayer0($: any): Promise<string | null> {
  try {
    const homeVar = str(await $.env.get('HOME').catch(() => undefined)) ?? str(await $.env.get('USERPROFILE').catch(() => undefined))
    const home = (homeVar ?? '').replace(/\\/g, '/')
    const mainSetting = str(await $.env.get('LAYER0_FILE').catch(() => undefined))
    const main = expandHome(mainSetting ?? DEFAULT_LAYER0_FILE, home)
    let text: string = await $.fs.read(main)
    if (!text || !text.trim()) return null
    const zoneFile = str(await $.env.get('LAYER0_ZONE_FILE').catch(() => undefined))
    const zoneMatch = str(await $.env.get('LAYER0_ZONE_MATCH').catch(() => undefined))
    if (zoneFile && zoneMatch) {
      const cwd: string = String((await $.session.cwd()) ?? '')
      if (cwd.includes(zoneMatch)) {
        try { text = text + '\n' + (await $.fs.read(expandHome(zoneFile, home))) } catch { /* no zone file: main only */ }
      }
    }
    return text
  } catch {
    return null
  }
}

// Plugins are stacked outer-to-inner in enable order. hook-daemon sits outside and answers UserPromptSubmit itself
// (it does not call next), so the classic hook below never runs. We therefore publish "the text placed" and
// "loaded at startup?" in $.state, and hook-daemon reads it and strips. The classic hook below stays for when
// hook-daemon falls back to next(), and for the reverse order.
const COMPOSED = { plugin: 'layer0-compose', key: 'composed' } as const
const STARTUP = { plugin: 'layer0-compose', key: 'startup' } as const

async function publish($: any) {
  try {
    await $.state.set(STARTUP, startupLoad)
    await $.state.set(COMPOSED, composed)
  } catch { /* if it cannot be written, hook-daemon simply does not strip (text appears twice) */ }
}

export const register: Register = (on) => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'layer0-compose', description: 'Status of Layer 0 in the system prompt (times placed / stripped from injection)' })
    try {
      startupLoad = isStartupLoad(await $.clock.now(), (await $.session.usage()).startedAt)
    } catch {
      startupLoad = false // cannot tell: lean towards not stripping
    }
    // On reload $.state keeps the previous value, so always overwrite here (mid-session load -> startup=false)
    composed = null
    await publish($)
    return next(e)
  })

  on('prompt.compose', async ($, e, next) => {
    const r = await next(e)
    const text = await readLayer0($)
    if (!text) {
      if (composed !== null) { composed = null; await publish($) }
      return r
    }
    if (text !== composed) { composed = text; await publish($) }
    stats.composed++
    return { sections: [...r.sections, { id: 'layer0-compose:layer0', text: SECTION_HEAD + text, scope: 'session' as const }] }
  })

  on('classic.UserPromptSubmit', async ($, e, next) => {
    const r: any = await next(e)
    if (!startupLoad || !composed || !r || !Array.isArray(r.additionalContext)) return r
    const out = strip(r.additionalContext, composed)
    if (!out) { stats.missed++; return r }
    stats.stripped++
    return { ...r, additionalContext: out }
  })

  on('command.run', { command: 'layer0-compose' }, async () => ({
    text:
      `placed in system prompt ${stats.composed} times / stripped from injection ${stats.stripped} / ` +
      `not stripped ${stats.missed} (when not stripped it only appears twice). Currently ${composed ? 'active' : 'inactive (rule file not loaded)'}; ` +
      `${startupLoad ? 'loaded at startup = strips injection' : 'loaded mid-session = does not strip'}`,
  }))
}
