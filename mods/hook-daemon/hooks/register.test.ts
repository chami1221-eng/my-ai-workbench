import { test, expect } from 'claude-code/testing'
import {
  toClassicResult, buildGroups, plan, toolText, asyncOnly, stripLayer0Chunk, L0_HOOK_PREFIX,
  parseScriptList, toolSpec, launchArgv,
} from './register'

test('async-logger-only tools go to the resident process; any sync hook or unknown async hook means hands off', () => {
  const g = buildGroups({
    PostToolUse: [
      { matcher: 'Bash', hooks: [{ type: 'command', command: 'python "$HOME/.claude/scripts/posttool-hooks.py"' }] },
      { matcher: 'Read|Grep', hooks: [{ type: 'command', command: 'python memory-access-logger.py', async: true }] },
      { matcher: 'Glob', hooks: [{ type: 'command', command: 'python other-logger.py', async: true }] },
    ],
  })
  expect(asyncOnly(g, 'PostToolUse', 'Read')).toEqual([{ script: 'memory-access-logger.py', command: 'python memory-access-logger.py' }])
  expect(asyncOnly(g, 'PostToolUse', 'Bash')).toBeNull()   // has a sync dispatcher
  expect(asyncOnly(g, 'PostToolUse', 'Glob')).toBeNull()   // async hook not on the allowlist
  expect(asyncOnly(g, 'PostToolUse', 'Write')).toBeNull()  // no hook at all
  expect(asyncOnly(null, 'PostToolUse', 'Read')).toBeNull()
})

const R = (code: number, stdout = '', stderr = '') => ({ code, stdout, stderr, ms: 1 })
const pre = (d: object) => JSON.stringify({ hookSpecificOutput: { hookEventName: 'PreToolUse', ...d } })

test('exit 2 is deny for PreToolUse and block for other events (reason from stderr)', () => {
  expect(toClassicResult('PreToolUse', R(2, '', 'no'))).toEqual({ deny: 'no' })
  expect(toClassicResult('Stop', R(2, '', 'fix it'))).toEqual({ block: 'fix it' })
  expect(toClassicResult('PostToolUse', R(2, '', 'x'))).toEqual({ block: 'x' })
})

test('JSON permissionDecision is copied', () => {
  expect(toClassicResult('PreToolUse', R(0, pre({ permissionDecision: 'deny', permissionDecisionReason: 'forbidden' })))).toEqual({ deny: 'forbidden' })
  expect(toClassicResult('PreToolUse', R(0, pre({ permissionDecision: 'ask', permissionDecisionReason: 'confirm' })))).toEqual({ ask: 'confirm' })
  expect(toClassicResult('PreToolUse', R(0, pre({ additionalContext: 'note' })))).toEqual({ additionalContext: ['note'] })
})

test('empty output means "do nothing" (same as a silent settings hook)', () => {
  expect(toClassicResult('PreToolUse', R(0, ''))).toEqual({})
  expect(toClassicResult('Stop', R(0, 'plain text is dropped for Stop'))).toEqual({})
})

test('plain text and additionalContext become context for UserPromptSubmit', () => {
  expect(toClassicResult('UserPromptSubmit', R(0, 'Layer0'))).toEqual({ additionalContext: ['Layer0'] })
  const j = JSON.stringify({ hookSpecificOutput: { hookEventName: 'UserPromptSubmit', additionalContext: 'L0' } })
  expect(toClassicResult('UserPromptSubmit', R(0, j))).toEqual({ additionalContext: ['L0'] })
})

test('read-only tool: exit 0 is a result, non-zero is an error with the reason', () => {
  expect(toolText(R(0, '{"a":1}\n'))).toEqual({ ok: true, text: '{"a":1}' })
  expect(toolText(R(0, ''))).toEqual({ ok: true, text: '(no output)' })
  expect(toolText(R(2, '', 'write subcommands are rejected'))).toEqual({ ok: false, text: 'exit 2\nwrite subcommands are rejected' })
})

test('read-only tool: script allowlist comes from env, with a neutral default', () => {
  expect(parseScriptList(undefined)).toEqual(['web-search.py', 'calendar-read.py', 'notes-read.py'])
  expect(parseScriptList(' a.py, b.py ,,')).toEqual(['a.py', 'b.py'])
  const spec = toolSpec(['a.py'])
  expect((spec.inputSchema.properties.name as any).enum).toEqual(['a.py'])
  expect(spec.description).toContain('a.py')
})

test('launch argv embeds the configured script path', () => {
  const argv = launchArgv('python3', '~/srv/hook_daemon.py')
  expect(argv[0]).toBe('python3')
  expect(argv[argv.length - 1]).toContain('"~/srv/hook_daemon.py"')
})

test('crashes (exit 1 etc.) are not answered here; they go to the normal path', () => {
  expect(toClassicResult('PreToolUse', R(1, '', 'Traceback'))).toBeUndefined()
})

test('decision:block / continue:false', () => {
  expect(toClassicResult('Stop', R(0, JSON.stringify({ decision: 'block', reason: 'r' })))).toEqual({ block: 'r' })
  expect(toClassicResult('Stop', R(0, JSON.stringify({ continue: false, stopReason: 's' })))).toEqual({ preventContinuation: true, stopReason: 's' })
})

const SETTINGS = {
  PreToolUse: [{ matcher: 'Write|Edit|Bash', hooks: [{ type: 'command', command: 'python "$HOME/.claude/scripts/pretool-hooks.py"' }] }],
  PostToolUse: [
    { matcher: 'Bash', hooks: [{ type: 'command', command: 'python "$HOME/.claude/scripts/posttool-hooks.py"' }] },
    { matcher: 'Read|Grep', hooks: [{ type: 'command', command: 'python logger.py', async: true }] },
  ],
  Stop: [
    { hooks: [{ type: 'command', command: 'python "$HOME/.claude/scripts/stop-hooks.py"' }] },
    { hooks: [{ type: 'command', command: 'pwsh notify-done.ps1', async: true }] },
  ],
}

test('answers only when the sync hooks are exactly one dispatcher', () => {
  const g = buildGroups(SETTINGS)
  expect(plan(g, 'PreToolUse', 'Bash')).toEqual([])
  expect(plan(g, 'PreToolUse', 'Read')).toBeNull() // no settings hook runs for this tool
  expect(plan(g, 'PostToolUse', 'Bash')).toEqual([])
  expect(plan(g, 'PostToolUse', 'Read')).toBeNull() // async logger only -> hands off
  expect(plan(g, 'Stop')).toEqual(['pwsh notify-done.ps1']) // async notification is re-fired
  expect(plan(g, 'UserPromptSubmit')).toBeNull() // not in the layout
  expect(plan(null, 'PreToolUse', 'Bash')).toBeNull()
})

test('one extra hook means no answer', () => {
  const more = { PreToolUse: [...SETTINGS.PreToolUse, { matcher: 'Bash', hooks: [{ type: 'command', command: 'python other.py' }] }] }
  expect(plan(buildGroups(more), 'PreToolUse', 'Bash')).toBeNull()
  expect(plan(buildGroups(more), 'PreToolUse', 'Write')).toEqual([])
})

test('Layer 0 is stripped only on an exact match with the text layer0-compose placed (trailing context stays)', () => {
  const L0 = '- rule A\n- rule B\n'
  const CH = (L0_HOOK_PREFIX + L0).trim()
  expect(stripLayer0Chunk([CH], L0)).toEqual([])
  expect(stripLayer0Chunk([CH + '\n\n📌 memory notice'], L0)).toEqual(['📌 memory notice'])
  expect(stripLayer0Chunk([(L0_HOOK_PREFIX + '- edited\n').trim()], L0)).toBeNull()
  expect(stripLayer0Chunk(['📌 only'], L0)).toBeNull()
})
