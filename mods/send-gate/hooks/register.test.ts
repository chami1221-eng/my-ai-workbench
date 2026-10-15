import { test, expect } from 'claude-code/testing'
import { judge, sendCallOf, coverage } from './gate'
import type { Msg } from './gate'

const BODY = 'Hi Sato,\nThanks for the update on the ACME project.\nWe expect the March renewal to keep the current setup.'
const A = (text: string): Msg => ({ role: 'assistant', text })
const U = (text: string): Msg => ({ role: 'user', text })

test('shown -> user reply -> draft passes (code blocks and quote marks are ignored)', () => {
  expect(judge([A('Revised:\n```\n' + BODY + '\n```\nSay OK and I will draft it'), U('OK')], BODY)).toEqual({ ok: true, via: 'shown+reply' })
  expect(judge([A(BODY.split('\n').map(l => '> ' + l).join('\n')), U('c')], BODY).ok).toBe(true)
})

test('drafting before showing, before the reply, or after adding sentences is stopped', () => {
  expect(judge([U('reply to Sato')], BODY).ok).toBe(false)
  const r = judge([U('reply to Sato'), A(BODY)], BODY)
  expect(r.ok).toBe(false)
  expect((r as any).reason).toContain('replied')
  const added = BODY + '\nI also have a call with ACME today and will follow up on the renewal after that.'
  expect(judge([A(BODY), U('OK')], added).ok).toBe(false)
})

test('edits the user dictated pass when their message covers the new wording', () => {
  const edited = BODY.replace('to keep the current setup.', 'with no plans to cancel.')
  expect(judge([A(BODY), U('Say "with no plans to cancel." instead. Then draft it')], edited).ok).toBe(true)
})

test('a body the user wrote passes; tool results and system injections do not count as a reply', () => {
  expect(judge([U('Draft this text\n' + BODY)], BODY)).toEqual({ ok: true, via: 'user-wrote' })
  expect(judge([A(BODY), { role: 'user', text: '', hasToolResults: true }], BODY).ok).toBe(false)
  expect(judge([A(BODY), U('<task-notification>done</task-notification>')], BODY).ok).toBe(false)
})

test('chat mention markup is left out of the comparison', () => {
  expect(coverage('<@U0123ABCD>\n' + BODY, '@Sato\n' + BODY)).toBeGreaterThan(0.95)
})

test('Bash: only calls that really create something are picked up (dry runs are ignored)', () => {
  expect(sendCallOf('python ~/scripts/email-draft.py --msg-id x --body-file a.txt')).toBeNull()
  expect(sendCallOf('python ~/scripts/email-draft.py --msg-id x --body-file "out/data/a b.txt" --commit')).toEqual({
    kind: 'email draft', body: undefined, bodyFile: 'out/data/a b.txt',
  })
  expect(sendCallOf("python g/email-draft.py --body 'text' --commit")!.body).toBe('text')
  expect(sendCallOf('python post-message.py post --channel general --text-file t.txt --confirm')!.bodyFile).toBe('t.txt')
  expect(sendCallOf('python post-message.py post --channel general --text-file t.txt')).toBeNull()
})

test('Bash: the sender list is configurable', () => {
  const custom = [{ kind: 'sms', match: [/sms\.sh/, /--send\b/], bodyFlag: '--text' }]
  expect(sendCallOf('sms.sh --to 1 --text hi --send', custom)).toEqual({ kind: 'sms', body: 'hi', bodyFile: undefined })
  expect(sendCallOf('python ~/scripts/email-draft.py --body x --commit', custom)).toBeNull()
})

test('tool.call: an unshown chat draft is stopped, a shown one is passed down', async ($, on) => {
  let msgs: any[] = [{ role: 'user', text: 'reply to them', toolUses: [] }]
  const store: Record<string, unknown> = {}
  on('session.messages', () => ({ value: msgs }))
  on('store.get', (_$, e) => ({ value: store[e.key] }))
  on('store.set', (_$, e) => ((store[e.key] = e.value), { value: undefined }))
  on('tool.call', { tool: 'mcp__slack__slack_send_message_draft' }, () => ({ result: { ok: true } }) as any)
  const r1: any = await $.tool.call({ tool: 'mcp__slack__slack_send_message_draft', channel_id: 'C1', message: BODY } as any)
  expect(r1.deny).toContain('send-gate')
  msgs = [...msgs, { role: 'assistant', text: BODY, toolUses: [] }, { role: 'user', text: 'OK', toolUses: [] }]
  const r2: any = await $.tool.call({ tool: 'mcp__slack__slack_send_message_draft', channel_id: 'C1', message: BODY } as any)
  expect(r2.deny).toBeUndefined()
})
