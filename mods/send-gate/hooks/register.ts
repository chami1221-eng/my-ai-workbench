import type { Register } from 'claude-code'

import { judge, sendCallOf } from './gate'
import type { Msg } from './gate'

// Enforces "(1) show the full text in the conversation -> (2) the user's OK -> (3) create the draft / send".
// For every call that creates a draft or posts, checks that its body appeared in full earlier in the conversation
// and that the user said something after that.
// Why a mod / risk: the permission prompt for these tools does not show the message body, so "Yes" there is not
// a real approval of the text. A sender script without a settings hook depends on attention alone.
//
// Replay over 14 days of sessions: 63 of 71 chat drafts pass; the 8 that would be stopped were either
// "drafted before showing the text" or "added sentences after it was shown".

// ---- Config ------------------------------------------------------------------
// Tools whose call carries an outbound body. `bodyField` = the input field holding the text.
// The defaults are the draft / schedule / send tools of a chat MCP server (e.g. Slack). Add your own.
export const GATED_TOOLS: Record<string, { label: string; bodyField: string }> = {
  mcp__slack__slack_send_message_draft: { label: 'chat draft', bodyField: 'message' },
  mcp__slack__slack_schedule_message: { label: 'scheduled chat message', bodyField: 'message' },
  mcp__slack__slack_send_message: { label: 'chat message', bodyField: 'message' },
}
// Bash-based senders are configured in gate.ts (BASH_SENDERS).
// ------------------------------------------------------------------------------

type Stats = { allowed: number; denied: number }

async function history($: any): Promise<Msg[]> {
  const rows = await $.session.messages()
  return rows.map((r: any) => ({ role: r.role, text: r.text ?? '', hasToolResults: !!r.toolResults?.length }))
}

const str = (v: unknown) => (typeof v === 'string' && v ? v : undefined)

// $.env.get needs a literal name (the SDK lists which variables a module reads)
async function homeDir($: any): Promise<string> {
  const v = str(await $.env.get('HOME').catch(() => undefined)) ?? str(await $.env.get('USERPROFILE').catch(() => undefined))
  return v ? v.replace(/\\/g, '/') : ''
}

async function resolvePath($: any, p: string): Promise<string> {
  if (p.startsWith('~/')) return (await homeDir($)) + p.slice(1)
  if (/^\/[a-z]\//.test(p)) return p[1].toUpperCase() + ':' + p.slice(2) // Git Bash /c/... -> C:/...
  if (/^[A-Za-z]:[\\/]/.test(p) || p.startsWith('/')) return p
  return (await $.session.cwd()).replace(/\\/g, '/') + '/' + p
}

async function count($: any, key: keyof Stats) {
  const s = ((await $.store.get('stats')) as Stats | undefined) ?? { allowed: 0, denied: 0 }
  s[key]++
  await $.store.set('stats', s)
}

function denyText(kind: string, reason: string, best: number): string {
  return (
    `🚫 send-gate: stopped ${kind}: ${reason} (best match with a shown text: ${Math.round(best * 100)}%).\n` +
    'Three steps: (1) show the exact full body in the conversation, (2) wait for the user to reply, ' +
    '(3) create the draft with that same body.\n' +
    'If you changed the text after the reply, show the full edited text again before running this.'
  )
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'send-gate', description: 'How many outbound calls send-gate allowed / stopped (cumulative)' })
    return next(e)
  })

  on('command.run', { command: 'send-gate' }, async $ => {
    const s = ((await $.store.get('stats')) as Stats | undefined) ?? { allowed: 0, denied: 0 }
    return { text: `send-gate total: allowed ${s.allowed} / stopped ${s.denied}` }
  })

  // One unfiltered handler so the tool list can live in config. Non-matching tools pass straight through.
  on('tool.call', async ($, e: any, next) => {
    const gated = GATED_TOOLS[String(e.tool)]
    if (gated) {
      const v = judge(await history($), String(e[gated.bodyField] ?? ''))
      if (!v.ok) {
        await count($, 'denied')
        return { deny: denyText(gated.label, v.reason, v.best) }
      }
      await count($, 'allowed')
      return next(e)
    }

    if (e.tool !== 'Bash') return next(e)
    const call = sendCallOf(String(e.command ?? ''))
    if (!call) return next(e)
    let body = call.body
    if (body === undefined && call.bodyFile) {
      body = await $.fs.read(await resolvePath($, call.bodyFile)).catch(() => undefined)
      if (body === undefined) {
        await count($, 'denied')
        return {
          deny: `🚫 send-gate: could not read the body file of ${call.kind} (${call.bodyFile}) before running. Create the body file first with the Write tool, then run the command in a separate Bash call (creating it inside the same command cannot be checked).`,
        }
      }
    }
    if (body === undefined) return next(e) // calls that take no body (--list etc.) are not gated
    const v = judge(await history($), body)
    if (!v.ok) {
      await count($, 'denied')
      return { deny: denyText(call.kind, v.reason, v.best) }
    }
    await count($, 'allowed')
    return next(e)
  })
}
