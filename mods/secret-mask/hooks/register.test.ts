import { test, expect } from 'claude-code/testing'
import { scrubText, scrubBlocks } from './register'

// Fake values are assembled at runtime (no realistic-looking secrets in the source)
const rep = (s: string, n: number) => s.repeat(n)
const GH = 'gh' + 'p_' + rep('Ab1Cd2Ef3G', 4)
const XOXB = 'xox' + 'b-' + '1234567890-' + rep('aB3', 8)
const APIKEY = rep('Xy7Zq', 6)
const GOOG = 'ya' + '29.' + rep('a0AfH6SM', 4)

test('shape-recognisable tokens are masked and the surrounding text stays', () => {
  const r = scrubText(`Access Token: ${GH}\nInstance: https://acme.example.com`)
  expect(r.text).toContain('[REDACTED:github-token]')
  expect(r.text).toContain('Instance: https://acme.example.com')
  expect(r.text).not.toContain(GH)
  expect(scrubText(`token ${XOXB} end`).text).toBe('token [REDACTED:slack-token] end')
  expect(scrubText(GOOG).kinds).toEqual(['google-oauth'])
  expect(scrubText(`Authorization: Bearer ${GOOG}`).text).not.toContain('a0AfH6SM')
})

test('key-name rules keep the key and mask only the value', () => {
  expect(scrubText(`MY_SERVICE_API_KEY=${APIKEY}`).text).toBe('MY_SERVICE_API_KEY=[REDACTED:env-secret]')
  expect(scrubText('MY_SERVICE_TOKEN=[REDACTED:env-secret]').text).toBe('MY_SERVICE_TOKEN=[REDACTED:env-secret]')
  expect(scrubText('{"accessToken": "[REDACTED:kv-secret]"}').text).toBe('{"accessToken": "[REDACTED:kv-secret]"}')
})

test('non-secrets are left alone (record IDs, UUIDs, short values, digits only, docs that mention prefixes)', () => {
  for (const s of [
    'record 0A1b2C3d4E5f6G7h8 updated',
    'session_id: 8dd195f2-bdde-42ac-a0c3-f33355cf4934',
    'token: 5000',
    'password: hogehoge',
    'max_tokens=128000000000000',
    'xo' + 'xc- tokens are banned in this workspace',
    'store X_AUTH_TOKEN/X_CT0 in ~/.x_credentials',
    // false positives found when replaying old transcripts
    "{'bridgeSessionId': 'session_01AbCdEf23GhIj45KlMn'}",
    'https://example.com/pubGroupTop.do;jsessionid=A1B2C3D4E5F6G7H8I9J0?methodName=x',
    'https://bucket.s3.amazonaws.com/f?X-Amz-Credential=' + 'AK' + 'IA' + 'ABCDEFGHIJ123456' + '%2F20260928',
  ]) {
    expect(scrubText(s)).toEqual({ text: s, kinds: [] })
  }
})

test('tokens split by ANSI color codes are still masked', () => {
  const colored = 'Access Token  \x1b[1m' + GH.slice(0, 10) + '\x1b[22m' + GH.slice(10)
  const r = scrubText(colored)
  expect(r.kinds).toEqual(['github-token'])
  expect(r.text).not.toContain(GH.slice(10))
})

test('the Bash result record (stdout) itself is masked; untouched results are returned as-is', async ($, on) => {
  let out = `fake ${XOXB}`
  on('tool.call', { tool: 'Bash' }, () => ({ ref: 1, result: { stdout: out, stderr: '', interrupted: false }, text: out }) as any)
  const r: any = await $.tool.call({ tool: 'Bash', command: 'echo x' } as any)
  expect(r.result.stdout).toBe('fake [REDACTED:slack-token]')
  expect(r.ref).toBeUndefined()
  out = 'nothing secret'
  const r2: any = await $.tool.call({ tool: 'Bash', command: 'echo y' } as any)
  expect(r2.result.stdout).toBe('nothing secret')
})

test('tool_result strings/arrays and text blocks are masked; other blocks are untouched', () => {
  const blocks = [
    { type: 'tool_result', tool_use_id: 't1', content: `out ${XOXB}` },
    { type: 'tool_result', tool_use_id: 't2', content: [{ type: 'text', text: GH }, { type: 'image', source: {} }] },
    { type: 'text', text: 'all good' },
    { type: 'tool_use', id: 'u1', name: 'Bash', input: { command: `echo ${XOXB}` } },
  ]
  const r = scrubBlocks(blocks)
  expect(r.kinds.sort()).toEqual(['github-token', 'slack-token'])
  expect(r.blocks[0].content).toBe('out [REDACTED:slack-token]')
  expect(r.blocks[1].content[0].text).toBe('[REDACTED:github-token]')
  expect(r.blocks[1].content[1]).toBe(blocks[1].content[1])
  expect(r.blocks[2]).toBe(blocks[2])
  expect(r.blocks[3]).toBe(blocks[3]) // tool_use is restored by the engine anyway, so it is not touched
})
