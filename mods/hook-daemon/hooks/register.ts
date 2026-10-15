import type { Register } from 'claude-code'

// Answer heavy Python command hooks from settings.json using a resident process (see README for the protocol).
//
// Safety: if the resident process is unavailable, does not answer, or the dispatcher crashes, always call next(e).
//   next(e) leads to the command hooks in settings.json (the usual one-Python-per-call path), so even if this mod
//   breaks, the same checks still run. There is no path where a guard is skipped.
//
// The reply is interpreted with the same rules Claude Code applies to command hooks:
//   exit 2           -> deny for PreToolUse, block for other events (reason from stderr)
//   exit 0 + JSON    -> copy continue:false / decision:block / hookSpecificOutput
//   exit 0 + text    -> passed as context for UserPromptSubmit only (dropped for other events)
//   any other exit   -> this mod does not answer; next(e) re-runs the normal path

// ---- Config ------------------------------------------------------------------
// Path of the resident server script. Override with env HOOK_DAEMON_SCRIPT.
const DEFAULT_DAEMON_SCRIPT = '~/.claude/scripts/hook_daemon.py'
// Python executable. Override with env HOOK_DAEMON_PYTHON.
const DEFAULT_PYTHON = 'python'
// One "dispatcher" script per event. This mod only answers when the sync hook for an event is exactly this one script.
const DISPATCHER_FILE: Record<string, string> = {
  PreToolUse: 'pretool-hooks.py',
  PostToolUse: 'posttool-hooks.py',
  Stop: 'stop-hooks.py',
  UserPromptSubmit: 'prompt-hooks.py',
}
// Async hooks the resident process may run on our behalf (keep in sync with the server's own allowlist).
const ASYNC_KNOWN = ['memory-access-logger.py']
// Scripts advertised to the model through the read-only `run` tool. Override with env HOOK_DAEMON_TOOL_SCRIPTS
// (comma separated). The server MUST enforce its own allowlist and reject write subcommands; this list only
// tells the model what exists. These names are neutral examples.
const DEFAULT_TOOL_SCRIPTS = ['web-search.py', 'calendar-read.py', 'notes-read.py']
const TIMEOUT_MS = 120_000
// ------------------------------------------------------------------------------

type Conn = { port: number; token: string }
type DaemonReply = { code: number; stdout: string; stderr: string; ms: number }

export function launchArgv(python: string, script: string): string[] {
  const lit = JSON.stringify(script) // a valid Python string literal for plain paths
  return [
    python, '-X', 'utf8', '-c',
    `import os,runpy,sys;sys.argv=['hook_daemon.py','--serve'];` +
      `runpy.run_path(os.path.expanduser(${lit}),run_name='__main__')`,
  ]
}

let conn: Conn | null = null
let dead = false // after a timeout or transport failure, this session uses the normal path only
let transcriptPath = ''
let sessionId = ''
const stats = { fast: 0, fallback: 0, ms: 0 }

// While the read-only `run` tool is executing, the resident process is busy (single thread + lock). Queuing a hook
// behind it would hit the host's 30 s $.http.fetch limit, set `dead`, and push the whole session to the slow path.
// While busy, hooks skip the resident process and go to the normal path. If `run` timed out, the server keeps
// working, so we stay "busy" until a GET returns.
let toolInFlight = 0
let draining = false
const busy = () => toolInFlight > 0 || draining
let busySkips = 0

async function waitIdle($: any) {
  if (draining) return
  draining = true
  for (let i = 0; i < 20 && conn && !dead; i++) {
    try {
      const r = await $.http.fetch(`http://127.0.0.1:${conn.port}/`, { headers: { 'X-Token': conn.token } })
      if (r.ok) break
    } catch {}
  }
  draining = false
}

async function ask($: any, event: string, payload: Record<string, unknown>): Promise<DaemonReply | null> {
  if (!conn || dead) return null
  if (busy()) { busySkips++; return null }
  const url = `http://127.0.0.1:${conn.port}/`
  const req = $.http.fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Token': conn.token },
    body: JSON.stringify({ event, payload, env: sessionId ? { CLAUDE_CODE_SESSION_ID: sessionId } : {} }),
  }).then((r: any) => (r.ok ? JSON.parse(r.text) : null))
  const timeout = $.clock.sleep(TIMEOUT_MS).then(() => 'timeout')
  try {
    const got = await Promise.race([req, timeout])
    if (got === 'timeout' || !got || typeof got.code !== 'number') {
      dead = true
      $.ui.log(`hook-daemon: no answer for ${event}; falling back to settings hooks`, { to: 'debug' })
      return null
    }
    return got as DaemonReply
  } catch (err) {
    dead = true
    $.ui.log(`hook-daemon: ${event} transport failure ${String(err)}; falling back to settings hooks`, { to: 'debug' })
    return null
  }
}

function parseJson(stdout: string): any | undefined {
  const s = stdout.trim()
  if (!s.startsWith('{')) return undefined
  try {
    return JSON.parse(s)
  } catch {
    return undefined
  }
}

// Turn a command hook's (exit, stdout, stderr) into a classic-event answer. undefined = do not answer here.
export function toClassicResult(event: string, r: DaemonReply): Record<string, unknown> | undefined {
  if (r.code === 2) {
    const reason = r.stderr.trim() || `${event} hook blocked`
    return event === 'PreToolUse' ? { deny: reason } : { block: reason }
  }
  if (r.code !== 0) return undefined
  const j = parseJson(r.stdout)
  const res: Record<string, unknown> = {}
  if (j === undefined) {
    const text = r.stdout.trim()
    if (text && event === 'UserPromptSubmit') res.additionalContext = [text]
    return res
  }
  if (j.continue === false) {
    res.preventContinuation = true
    if (typeof j.stopReason === 'string') res.stopReason = j.stopReason
  }
  const hso = j.hookSpecificOutput ?? {}
  if (event === 'PreToolUse') {
    const pd = hso.permissionDecision ?? (j.decision === 'block' ? 'deny' : j.decision === 'approve' ? 'allow' : undefined)
    const why = String(hso.permissionDecisionReason ?? j.reason ?? '')
    if (pd === 'deny') res.deny = why || 'denied by hook'
    else if (pd === 'ask') res.ask = why
    else if (pd === 'allow') res.allow = true
    if (hso.updatedInput && typeof hso.updatedInput === 'object') res.updatedInput = hso.updatedInput
  } else if (j.decision === 'block') {
    res.block = String(j.reason ?? '')
  }
  if (typeof hso.additionalContext === 'string' && hso.additionalContext) res.additionalContext = [hso.additionalContext]
  if (event === 'UserPromptSubmit') {
    if (typeof hso.sessionTitle === 'string') res.sessionTitle = hso.sessionTitle
    if (hso.suppressOriginalPrompt === true) res.suppressOriginalPrompt = true
  }
  return res
}

// ---- Matching against the hook layout in settings ------------------------------
// When this mod answers, ALL settings hooks for that event stop running (including other hooks on the same matcher).
// So it only answers when "the sync hooks for this event x tool are exactly one dispatcher".
// Async hooks (desktop notifications etc.) are re-fired by us after answering (via bash, not awaited, like settings).
// If the layout is not what we expect (a hook was added, settings unreadable), use the normal path.

type Cmd = { command: string; async: boolean }
type Group = { matcher: RegExp; cmds: Cmd[] }
let groups: Record<string, Group[]> | null = null

function toMatcher(m: unknown): RegExp {
  const s = typeof m === 'string' ? m.trim() : ''
  if (!s || s === '*') return /^/
  try {
    return new RegExp(`^(?:${s})$`)
  } catch {
    return /^/
  }
}

export function buildGroups(hooks: any): Record<string, Group[]> {
  const out: Record<string, Group[]> = {}
  for (const ev of Object.keys(hooks ?? {})) {
    out[ev] = (hooks[ev] ?? []).map((g: any) => ({
      matcher: toMatcher(g?.matcher),
      cmds: (g?.hooks ?? []).map((h: any) => ({
        command: h?.type === 'command' || h?.command ? String(h.command ?? '') : `<${h?.type}>`,
        async: h?.async === true,
      })),
    }))
  }
  return out
}

// If we may answer: the async commands to re-fire. Otherwise null.
export function plan(gs: Record<string, Group[]> | null, ev: string, tool?: string): string[] | null {
  if (!gs) return null
  const matching = (gs[ev] ?? []).filter(g => (tool === undefined ? true : g.matcher.test(tool)))
  const cmds = matching.flatMap(g => g.cmds)
  const sync = cmds.filter(c => !c.async)
  const dispatcher = DISPATCHER_FILE[ev]
  if (!dispatcher || sync.length !== 1 || !sync[0].command.includes(dispatcher)) return null
  return cmds.filter(c => c.async).map(c => c.command)
}

type AsyncJob = { script: string; command: string }

// If the hooks for this event x tool are "zero sync, all async in ASYNC_KNOWN", return the jobs. Otherwise null.
export function asyncOnly(gs: Record<string, Group[]> | null, ev: string, tool: string): AsyncJob[] | null {
  if (!gs) return null
  const cmds = (gs[ev] ?? []).filter(g => g.matcher.test(tool)).flatMap(g => g.cmds)
  if (!cmds.length || cmds.some(c => !c.async)) return null
  const jobs: AsyncJob[] = []
  for (const c of cmds) {
    const script = ASYNC_KNOWN.find(s => c.command.includes(s))
    if (!script) return null
    jobs.push({ script, command: c.command })
  }
  return jobs
}

async function fireDaemonAsync($: any, job: AsyncJob, payload: unknown) {
  try {
    const res = await $.http.fetch(`http://127.0.0.1:${conn!.port}/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Token': conn!.token },
      body: JSON.stringify({ event: 'async', script: job.script, payload,
                             env: sessionId ? { CLAUDE_CODE_SESSION_ID: sessionId } : {} }),
    })
    if (res.ok && typeof JSON.parse(res.text).code === 'number') return
  } catch {}
  fireAsync($, [job.command], payload) // not run by the server: re-fire the same command settings would have run
}

// Known limitation: a child started with `void $.process.run(...)` is stopped when the hook returns.
// Anything that must survive should be started by the resident process instead (see README).
function fireAsync($: any, cmds: string[], payload: unknown) {
  for (const command of cmds) {
    void $.process
      .run(['bash', '-c', command], { stdin: JSON.stringify(payload), timeoutMs: 600_000 })
      .catch(() => {})
  }
}

function rememberBase(e: any) {
  if (typeof e?.transcript_path === 'string' && e.transcript_path) transcriptPath = e.transcript_path
  if (typeof e?.session_id === 'string' && e.session_id) sessionId = e.session_id
}

async function runClassic(ev: 'PostToolUse' | 'Stop' | 'UserPromptSubmit', $: any, e: any, next: any) {
  rememberBase(e)
  const tool = ev === 'PostToolUse' ? String(e.tool_name ?? '') : undefined
  const extra = plan(groups, ev, tool)
  if (!extra || !conn || dead) return next(e)
  const r = await ask($, ev, e)
  const res = r && toClassicResult(ev, r)
  if (!res) {
    stats.fallback++
    return next(e)
  }
  stats.fast++
  stats.ms += r!.ms
  fireAsync($, extra, e)
  return res
}

// ---- Cooperation with layer0-compose -------------------------------------------
// Plugins are stacked outer-to-inner in enable order, and hook-daemon sits outside. If it answers
// UserPromptSubmit itself, the inner layer0-compose classic hook never runs, and the Layer 0 rules would be sent twice
// (system prompt + per-turn injection). layer0-compose publishes "the text placed in the system prompt" and
// "was it loaded at startup" in $.state; we strip the injected copy only on an exact match.
// No layer0-compose, no value, or no match -> strip nothing (worst case the text appears twice, never zero times).
// The chunk format must match layer0-compose's hookChunk/strip (change both together).
export const L0_HOOK_PREFIX = '⚠️ Layer 0 (must not violate, check every turn):\n'
const L0_COMPOSED = { plugin: 'layer0-compose', key: 'composed' } as const
const L0_STARTUP = { plugin: 'layer0-compose', key: 'startup' } as const
const l0Stats = { stripped: 0, missed: 0, skipped: 0 }

export function stripLayer0Chunk(ctx: readonly string[], layer0: string): string[] | null {
  const chunk = (L0_HOOK_PREFIX + layer0).trim()
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

async function stripLayer0($: any, r: any): Promise<any> {
  if (!r || !Array.isArray(r.additionalContext) || !r.additionalContext.length) return r
  if (!r.additionalContext.some((s: unknown) => typeof s === 'string' && s.includes(L0_HOOK_PREFIX))) return r
  try {
    const [c, s] = await Promise.all([$.state.get(L0_COMPOSED), $.state.get(L0_STARTUP)])
    if (s.value !== true || typeof c.value !== 'string' || !c.value) { l0Stats.skipped++; return r }
    const out = stripLayer0Chunk(r.additionalContext, c.value)
    if (!out) { l0Stats.missed++; return r }
    l0Stats.stripped++
    return { ...r, additionalContext: out }
  } catch {
    l0Stats.skipped++
    return r
  }
}

// ---- Read-only tool answered inside the resident process ------------------------
// Bash is NOT intercepted: answering Bash in tool.call would skip PreToolUse hooks and permission checks.
// Instead a separate tool is registered and goes through the normal permission flow.
export const TOOL_NAME = 'run'

export function toolSpec(scripts: readonly string[]) {
  return {
    name: TOOL_NAME,
    description:
      'Run a read-only script in the resident process (faster than spawning it through Bash). ' +
      'It cannot write anything. op="script": run one of the allowlisted scripts with argv ' +
      `(same output as running it with python through Bash). Allowed: ${scripts.join(' / ')}. ` +
      'Write subcommands and flags such as --commit / --confirm / --out are rejected by the server. ' +
      'On error or if the script is not allowed, run the same thing through Bash as usual.',
    inputSchema: {
      type: 'object',
      properties: {
        op: { type: 'string', enum: ['script'] },
        name: { type: 'string', enum: [...scripts] },
        argv: { type: 'array', items: { type: 'string' }, description: 'arguments for op=script (without the script name)' },
      },
      required: ['op'],
    },
  }
}

export function parseScriptList(raw: string | undefined): string[] {
  const list = (raw ?? '').split(',').map(s => s.trim()).filter(Boolean)
  return list.length ? list : [...DEFAULT_TOOL_SCRIPTS]
}

const toolStats = { ok: 0, err: 0, ms: 0 }

export function toolText(r: DaemonReply): { ok: boolean; text: string } {
  const out = r.stdout.trim()
  const err = r.stderr.trim()
  if (r.code === 0) return { ok: true, text: out || '(no output)' }
  return { ok: false, text: `exit ${r.code}\n${err || out || '(no output)'}` }
}

// $.env.get needs a literal name (the SDK lists which variables a module reads), so each call is spelled out
const str = (v: unknown) => (typeof v === 'string' && v ? v : undefined)

export const register: Register = on => {
  // Through ToolSearch the schema costs an extra round trip and the model tends to pick Bash instead.
  // List the tool directly.
  on('tool.describe', { tool: 'mcp__hook-daemon__run' }, async ($, e, next) => {
    const r = await next(e)
    return { ...r, isDeferred: false }
  })

  on('tool.call', { tool: 'mcp__hook-daemon__run' }, async ($, e) => {
    if (!conn) return { deny: 'The resident process is not running. Run the same thing through Bash.' }
    const { tool, tool_use_id, agentId, ...args } = e as any
    // Do not use ask(): on failure it sets `dead` and would also stop the fast hook path.
    // Tool failures are kept separate from hooks and the reason is returned to the model.
    let r: DaemonReply | null = null
    let why = ''
    toolInFlight++
    try {
      const res = await $.http.fetch(`http://127.0.0.1:${conn.port}/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Token': conn.token },
        body: JSON.stringify({ event: 'tool', payload: { ...args, cwd: await $.session.cwd() } }),
      })
      if (!res.ok) why = `HTTP ${res.status} ${String(res.text).slice(0, 300)}`
      else {
        const j = JSON.parse(res.text)
        if (typeof j.code === 'number') r = j
        else why = `unexpected reply ${String(res.text).slice(0, 300)}`
      }
    } catch (err) {
      why = `transport failure ${String(err).slice(0, 300)}`
      void waitIdle($) // we gave up waiting, but the server may still be working
    } finally {
      toolInFlight--
    }
    if (!r) {
      toolStats.err++
      $.ui.log(`hook-daemon: run failed ${why}`, { to: 'debug' })
      return { deny: `Could not run in the resident process (${why}). Run the same thing through Bash.` }
    }
    const { ok, text } = toolText(r)
    if (!ok) {
      toolStats.err++
      return { deny: text }
    }
    toolStats.ok++
    toolStats.ms += r.ms
    return { result: text }
  })

  on('session.start', async ($, e, next) => {
    const started = await next(e)
    $.command.register({ name: 'hook-daemon', description: 'Resident hook status (answered here vs. sent to the normal path)' })
    try {
      const scripts = str(await $.env.get('HOOK_DAEMON_TOOL_SCRIPTS').catch(() => undefined))
      await $.tool.register(toolSpec(parseScriptList(scripts)))
    } catch (err) {
      $.ui.log(`hook-daemon: tool registration failed ${String(err)}`, { to: 'debug' })
    }
    try {
      sessionId = await $.session.id()
    } catch {}
    try {
      groups = buildGroups(((await $.settings.read()) as any).hooks)
    } catch {
      groups = null // unreadable settings: never answer (everything goes to the normal path)
    }
    const python = str(await $.env.get('HOOK_DAEMON_PYTHON').catch(() => undefined))
    const script = str(await $.env.get('HOOK_DAEMON_SCRIPT').catch(() => undefined))
    const argv = launchArgv(python ?? DEFAULT_PYTHON, script ?? DEFAULT_DAEMON_SCRIPT)
    void (async () => {
      try {
        const child = $.process.spawn({ argv, env: sessionId ? { CLAUDE_CODE_SESSION_ID: sessionId } : {} })
        let buf = ''
        for await (const piece of child as any) {
          if (conn || piece.stream !== 'stdout') continue
          buf += piece.text
          const nl = buf.indexOf('\n')
          if (nl < 0) continue
          try {
            const j = JSON.parse(buf.slice(0, nl))
            conn = { port: j.port, token: j.token }
            $.ui.log(`hook-daemon: resident process up port=${j.port} pid=${j.pid}`, { to: 'debug' })
          } catch {
            dead = true
          }
        }
      } catch (err) {
        $.ui.log(`hook-daemon: failed to start ${String(err)}`, { to: 'debug' })
      }
      // Loop ended = child exited. From now on, normal path only.
      conn = null
      dead = true
    })()
    return started
  })

  on('classic.PreToolUse', async ($, e, next) => {
    const { tool, tool_use_id, mcp_server, agentId, ...input } = e as any
    const extra = plan(groups, 'PreToolUse', tool)
    if (!extra || !conn || dead) return next(e)
    const payload: Record<string, unknown> = {
      session_id: sessionId,
      transcript_path: transcriptPath,
      cwd: await $.session.cwd(),
      hook_event_name: 'PreToolUse',
      tool_name: tool,
      tool_input: input,
      tool_use_id,
    }
    const r = await ask($, 'PreToolUse', payload)
    const res = r && toClassicResult('PreToolUse', r)
    if (!res) {
      stats.fallback++
      return next(e)
    }
    stats.fast++
    stats.ms += r!.ms
    fireAsync($, extra, payload)
    return res as any
  })

  on('classic.PostToolUse', async ($, e, next) => {
    // Tools that have no sync hook and only async loggers (e.g. Read/Grep) are fired at the resident process
    // without waiting, removing the Python spawn. If the server is unavailable, the original command is re-fired.
    rememberBase(e)
    const jobs = asyncOnly(groups, 'PostToolUse', String((e as any).tool_name ?? ''))
    if (!jobs || !conn || dead || busy()) return runClassic('PostToolUse', $, e, next)
    for (const j of jobs) void fireDaemonAsync($, j, e)
    stats.fast++
    return {}
  })
  on('classic.Stop', ($, e, next) => runClassic('Stop', $, e, next))
  on('classic.UserPromptSubmit', async ($, e, next) => stripLayer0($, await runClassic('UserPromptSubmit', $, e, next)))

  on('command.run', { command: 'hook-daemon' }, async () => ({
    text:
      `${conn && !dead ? `resident on port=${conn.port}` : 'stopped (settings hooks are in use)'}\n` +
      `answered here ${stats.fast} times (avg ${stats.fast ? Math.round(stats.ms / stats.fast) : 0}ms) / normal path ${stats.fallback} times\n` +
      `read-only tool run: ok ${toolStats.ok} (avg ${toolStats.ok ? Math.round(toolStats.ms / toolStats.ok) : 0}ms) / errors ${toolStats.err}\n` +
      `hooks sent to the normal path because run was busy: ${busySkips}\n` +
      `Layer 0 injection: stripped ${l0Stats.stripped} / kept on mismatch ${l0Stats.missed} / kept because layer0-compose was off or loaded mid-session ${l0Stats.skipped}`,
  }))
}
