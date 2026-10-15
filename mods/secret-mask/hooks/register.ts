import type { Register } from 'claude-code'

// Redact tokens, API keys and passwords from rows entering the conversation, before they are stored.
//
// Risk: tokens printed by a command end up in the transcript and model context. A standing rule ("never print
// the output of a command that returns a token") depends on attention; this moves it into the pipeline.
// session.append is called once per row (tool result, hook context, input, reply, notification) right before
// it is stored. The content rewritten here is what the model and the transcript (jsonl) receive. Unlike
// tool.call, it does not depend on the order of other mods, and results of other plugin tools pass through it too.
// Note: the terminal may briefly show the original text (documented in the SDK types). Model and log never get it.
// Note: this is a safety net, not a permission. Keep not running commands that print secrets.

// ---- Config ------------------------------------------------------------------
const MASK = (kind: string) => `[REDACTED:${kind}]`
const MASK_PREFIX = '[REDACTED:'

type Rule = { kind: string; re: RegExp; keep?: number } // keep = group index to keep in front (e.g. a key name)

// AWS access key prefixes are assembled at runtime so the literal does not trip secret scanners on this file.
const AWS_PREFIXES = ['AK' + 'IA', 'AS' + 'IA'].join('|')

// Secrets recognisable by the shape of the value itself
const RULES: Rule[] = [
  { kind: 'private-key', re: /-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g },
  { kind: 'slack-token', re: /\bxox[abposrce]-[A-Za-z0-9-]{10,}/g },
  { kind: 'slack-app-token', re: /\bxapp-[A-Za-z0-9-]{10,}/g },
  { kind: 'google-oauth', re: /\bya29\.[A-Za-z0-9._-]{20,}/g },
  { kind: 'google-refresh', re: /\b1\/\/0[A-Za-z0-9_-]{30,}/g },
  { kind: 'google-api-key', re: /\bAIza[0-9A-Za-z_-]{35}\b/g },
  { kind: 'anthropic-key', re: /\bsk-ant-[A-Za-z0-9_-]{20,}/g },
  { kind: 'openai-key', re: /\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}/g },
  // X-Amz-Credential= in a presigned URL carries only the key ID (not secret); masking it would break the URL
  { kind: 'aws-access-key', re: new RegExp(`(?<!Credential=)\\b(?:${AWS_PREFIXES})[0-9A-Z]{16}\\b`, 'g') },
  { kind: 'github-token', re: /\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})/g },
  { kind: 'jwt', re: /\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/g },
  { kind: 'bearer', re: /(\bBearer\s+)([A-Za-z0-9._~+/=!-]{20,})/g, keep: 1 },
]

// Secrets recognisable by the key name (mask the value only, keep the key name).
// Only when the value is long enough and contains both letters and digits.
const KV_RULES: Rule[] = [
  {
    kind: 'env-secret',
    re: /\b([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|ACCESS_KEY|PRIVATE_KEY|CT0)[A-Z0-9_]*\s*=\s*["']?)([^\s"']{12,})/g,
    keep: 1,
  },
  {
    kind: 'kv-secret',
    // Names with an alphanumeric prefix (bridgeSessionId, jsessionid in URLs) are different things; excluded
    re: /((?<![A-Za-z0-9_])["']?(?:password|passwd|client_secret|secret|api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|auth[_-]?token|session[_-]?token|accessToken|refreshToken|authToken|sessionId|session_id)["']?\s*[:=]\s*["']?)([A-Za-z0-9._~+/=!-]{16,})/gi,
    keep: 1,
  },
]
// ------------------------------------------------------------------------------

const ANSI = /\x1b\[[0-9;?]*[ -\/]*[@-~]/g

function looksSecret(v: string): boolean {
  return /[A-Za-z]/.test(v) && /[0-9]/.test(v) && !/^[0-9a-f]{8}-[0-9a-f]{4}-/.test(v) // UUIDs (session IDs) are not secrets
}

export type ScrubResult = { text: string; kinds: string[] }

function applyRules(text: string, kinds: string[]): string {
  let out = text
  for (const r of RULES) {
    out = out.replace(r.re, (...m: any[]) => {
      kinds.push(r.kind)
      return r.keep ? `${m[r.keep]}${MASK(r.kind)}` : MASK(r.kind)
    })
  }
  for (const r of KV_RULES) {
    out = out.replace(r.re, (whole: string, head: string, val: string) => {
      if (val.startsWith(MASK_PREFIX) || !looksSecret(val)) return whole
      kinds.push(r.kind)
      return `${head}${MASK(r.kind)}`
    })
  }
  return out
}

// Redact one string. If nothing matched, return text unchanged with empty kinds.
export function scrubText(text: string): ScrubResult {
  const kinds: string[] = []
  const out = applyRules(text, kinds)
  if (!kinds.length && text.includes('\x1b[')) {
    // Colored CLI output can split a token with escape sequences. If stripping colors makes it match, mask the plain version.
    const plain = text.replace(ANSI, '')
    const k2: string[] = []
    const o2 = applyRules(plain, k2)
    if (k2.length) return { text: o2, kinds: k2 }
  }
  return { text: kinds.length ? out : text, kinds }
}

// Redact a list of content blocks (only text blocks and tool_result contents; everything else is untouched)
export function scrubBlocks(blocks: readonly any[]): { blocks: any[]; kinds: string[] } {
  const kinds: string[] = []
  const one = (s: string) => {
    const r = scrubText(s)
    kinds.push(...r.kinds)
    return r.text
  }
  const out = blocks.map((b: any) => {
    if (!b || typeof b !== 'object') return b
    if (b.type === 'text' && typeof b.text === 'string') {
      const t = one(b.text)
      return t === b.text ? b : { ...b, text: t }
    }
    if (b.type === 'tool_result') {
      if (typeof b.content === 'string') {
        const t = one(b.content)
        return t === b.content ? b : { ...b, content: t }
      }
      if (Array.isArray(b.content)) {
        let changed = false
        const inner = b.content.map((c: any) => {
          if (c && c.type === 'text' && typeof c.text === 'string') {
            const t = one(c.text)
            if (t !== c.text) { changed = true; return { ...c, text: t } }
          }
          return c
        })
        return changed ? { ...b, content: inner } : b
      }
    }
    return b
  })
  return { blocks: out, kinds }
}

const stats = { rows: 0, hits: 0, byKind: {} as Record<string, number> }

export const register: Register = (on) => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'secret-mask', description: 'Redaction status (rows and values masked, by kind)' })
    return next(e)
  })

  on('session.append', async ($, e, next) => {
    const content = e.message?.content
    if (!Array.isArray(content) || !content.length) return next(e)
    let res
    try {
      res = scrubBlocks(content)
    } catch {
      return next(e) // if redaction throws, let the row through (never stop the conversation)
    }
    if (!res.kinds.length) return next(e)
    stats.rows++
    stats.hits += res.kinds.length
    for (const k of res.kinds) stats.byKind[k] = (stats.byKind[k] ?? 0) + 1
    const where = (e.origin as any)?.kind === 'tool' ? `result of ${(e.origin as any).tool}` : e.door
    const kinds = [...new Set(res.kinds)].join(', ')
    $.ui.toast(`🔒 Masked ${res.kinds.length} secret value(s) (${where}: ${kinds})`)
    $.ui.log(`secret-mask: ${e.door} ${where} ${kinds}`, { to: 'debug' })
    return next({ ...e, message: { ...e.message, content: res.blocks } })
  })

  // session.append can only rewrite the model-facing form. The transcript's toolUseResult (raw record used for
  // display) still keeps the original value (verified in a live session). For Bash, mask the result record itself
  // in tool.call. Dropping ref and returning { result } makes the engine rebuild the model-facing form from it.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    const r: any = await next(e)
    if (!r || r.deny !== undefined || !r.result || typeof r.result !== 'object') return r
    const out: Record<string, unknown> = { ...r.result }
    let hit = false
    for (const k of ['stdout', 'stderr']) {
      const v = out[k]
      if (typeof v !== 'string' || !v) continue
      const s = scrubText(v)
      if (s.kinds.length) { out[k] = s.text; hit = true }
    }
    if (!hit) return r
    return r.isError ? { result: out, isError: true } : { result: out }
  })

  on('command.run', { command: 'secret-mask' }, async () => ({
    text:
      `Masked rows: ${stats.rows} / values: ${stats.hits}` +
      (stats.hits ? ` (${Object.entries(stats.byKind).map(([k, n]) => `${k} ${n}`).join(', ')})` : ''),
  }))
}
