// Pure functions that decide whether an outbound body "was shown in full in the conversation and then got a human reply".
// Enforces steps 1 and 2 of a three-step rule: (1) show the full text in the conversation, (2) get the user's OK,
// (3) only then create the draft / send.

export type Msg = { role: 'user' | 'assistant'; text: string; hasToolResults?: boolean }
export type Verdict = { ok: true; via: 'shown+reply' | 'user-wrote' } | { ok: false; reason: string; best: number }

// ---- Config ------------------------------------------------------------------
export const THRESHOLD = 0.9 // share of the body that must be covered by what was shown
const K = 8 // shorter matches than this count as coincidence
const WINDOW = 80 // how many recent messages to look back (avoid matching old drafts)

// Bash commands that create a draft or post. A command is gated when every regex in `match` matches.
// The body is taken from `bodyFlag` (inline) or `bodyFileFlag` (file read before the command runs).
// Dry runs (without the commit flag) are not gated. These script names are examples; replace with your own.
export type BashSender = { kind: string; match: RegExp[]; bodyFlag?: string; bodyFileFlag?: string }
export const BASH_SENDERS: BashSender[] = [
  { kind: 'email draft', match: [/email-draft\.py/, /(^|\s)--commit\b/], bodyFlag: '--body', bodyFileFlag: '--body-file' },
  { kind: 'team feed post', match: [/post-message\.py\b/, /\bpost\b/, /(^|\s)--confirm\b/], bodyFileFlag: '--text-file' },
]
// ------------------------------------------------------------------------------

// Drop display decoration (Markdown, quotes, bullets, full-width spaces) and whitespace before comparing
export function norm(s: string): string {
  return s
    .normalize('NFKC')
    .replace(/\\n/g, '\n')
    .replace(/[\s*_`>~#|・\-]/g, '')
}

// Chat mention markup is shown as @name on screen, so leave it out of the comparison
const stripMarkup = (s: string) => s.replace(/<@[A-Z0-9]+>/g, '').replace(/<(https?:[^|>]+)\|[^>]*>/g, '$1')

// Share of body characters found in `text` as runs of K+ consecutive characters.
// Line-by-line comparison would fail a whole line when the user asked to change one word, so count runs instead.
export function coverage(body: string, text: string): number {
  const b = norm(stripMarkup(body))
  if (!b.length) return 1
  const hay = norm(stripMarkup(text))
  let covered = 0
  let i = 0
  while (i < b.length) {
    if (b.length - i >= K && hay.includes(b.slice(i, i + K))) {
      let len = K
      while (i + len < b.length && hay.includes(b.slice(i, i + len + 1))) len++
      covered += len
      i += len
    } else i++
  }
  return covered / b.length
}

const isEmpty = (body: string) => norm(stripMarkup(body)).length < 2

export function isHuman(m: Msg): boolean {
  if (m.role !== 'user' || m.hasToolResults) return false
  const t = m.text.trim()
  return t !== '' && !t.startsWith('<') && !t.startsWith('[Request interrupted')
}

// Pass when the shown text (assistant) plus the user's later messages (including edit instructions) cover 90%+ of
// the body AND there is at least one user message after it. Also pass when the user wrote the body themselves.
export function judge(messages: readonly Msg[], body: string): Verdict {
  if (isEmpty(body)) return { ok: false, reason: 'the body is empty or unreadable', best: 0 }
  const recent = messages.slice(-WINDOW)
  const humans = recent.filter(isHuman)
  if (humans.length && coverage(body, humans.map(m => m.text).join('\n')) >= THRESHOLD) return { ok: true, via: 'user-wrote' }
  let best = 0
  let shownNoReply = false
  for (let i = recent.length - 1; i >= 0; i--) {
    const m = recent[i]
    if (m.role !== 'assistant') continue
    const after = recent.slice(i + 1).filter(isHuman)
    const c = coverage(body, [m.text, ...after.map(h => h.text)].join('\n'))
    best = Math.max(best, c)
    if (c < THRESHOLD) continue
    if (after.length) return { ok: true, via: 'shown+reply' }
    shownNoReply = true
  }
  return shownNoReply
    ? { ok: false, reason: 'the body was shown, but the user has not replied (approved) since', best }
    : { ok: false, reason: 'this body has not been shown in full in the conversation (if you edited it, show the edited full text)', best }
}

// ---------------------------------------------------------------- extracting the body from Bash

// Words with quotes removed (no operator splitting; just enough to read a sender script's arguments)
export function words(cmd: string): string[] {
  const out: string[] = []
  let i = 0
  while (i < cmd.length) {
    while (i < cmd.length && /\s/.test(cmd[i])) i++
    if (i >= cmd.length) break
    let w = ''
    while (i < cmd.length && !/\s/.test(cmd[i])) {
      const c = cmd[i]
      if (c === "'") {
        const end = cmd.indexOf("'", i + 1)
        if (end < 0) return out
        w += cmd.slice(i + 1, end)
        i = end + 1
      } else if (c === '"') {
        i++
        while (i < cmd.length && cmd[i] !== '"') {
          if (cmd[i] === '\\' && '"$`\\'.includes(cmd[i + 1] ?? '')) i++
          w += cmd[i++]
        }
        i++
      } else if (c === '\\' && i + 1 < cmd.length) {
        w += cmd[i + 1]
        i += 2
      } else {
        w += c
        i++
      }
    }
    out.push(w)
  }
  return out
}

export type SendCall = { kind: string; body?: string; bodyFile?: string }

function argOf(ws: string[], flag: string | undefined): string | undefined {
  if (!flag) return undefined
  for (let i = 0; i < ws.length; i++) {
    if (ws[i] === flag) return ws[i + 1]
    if (ws[i].startsWith(flag + '=')) return ws[i].slice(flag.length + 1)
  }
  return undefined
}

// Only pick up calls that really create a draft or post (dry runs are ignored)
export function sendCallOf(cmd: string, senders: readonly BashSender[] = BASH_SENDERS): SendCall | null {
  for (const s of senders) {
    if (!s.match.every(re => re.test(cmd))) continue
    const ws = words(cmd)
    return { kind: s.kind, body: argOf(ws, s.bodyFlag), bodyFile: argOf(ws, s.bodyFileFlag) }
  }
  return null
}
