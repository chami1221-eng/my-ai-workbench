import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { Level, Running } from '../types'

// One band above the prompt that shows up to three kinds of rows, only when needed (nothing to show = no band).
//  ⏳ tools running for 8 s or more ... so the user does not have to ask "still running?" (asked 7 times and
//     42 interruptions in 14 days)
//  c  if the last reply waits for approval, what a one-key "c" (continue/approve) would approve. In this setup "c"
//     was the most used input (241 times) and doubles as approval, so it helps to see what it approves.
//  ctx context usage with a wrap-up hint (thresholds 40/60/75%)
//
// Other mods' bands (e.g. shot-peek) are kept: our rows are stacked on top of next(e)'s result.

// ---- Config ------------------------------------------------------------------
const SLOW_MS = 8000 // show a running tool after this long
const MAX_ROWS = 3 // max running-tool rows
const APPROVE_KEY = 'c' // the one-key reply you use to approve / continue
// Lines that read as a question or request for approval (Japanese and English endings)
const ASK_RE =
  /(ますか|でしょうか|ください|いいですか|どれから|どちら|[?？])\s*[。）)]*\s*$|OKなら|よければ|「OK」で|\b(?:shall I|should I|OK to|okay to|let me know|say OK)\b/i
// Context-usage thresholds and the hint shown at each level
export function levelOf(pct: number | undefined): Level | null {
  if (pct === undefined || pct < 40) return null
  if (pct < 60) return { pct, action: 'save notes to memory' }
  if (pct < 75) return { pct, action: 'getting heavy, plan a handoff' }
  return { pct, action: 'write a handoff now (very full)' }
}
// ------------------------------------------------------------------------------

const running = atom({ plugin: 'prompt-band', key: 'running' } as const, [] as Running[])
const ask = atom({ plugin: 'prompt-band', key: 'ask' } as const, null as string | null)
const level = atom({ plugin: 'prompt-band', key: 'level' } as const, null as Level | null)
const hiddenLevel = atom({ plugin: 'prompt-band', key: 'hiddenLevel' } as const, null as string | null)

// From the last few lines of a reply, pick the one line that asks the user for an answer
export function askOf(answer: string): string | null {
  const lines = answer
    .split('\n')
    .map(l => l.replace(/^[\s>*\-・#]+/, '').replace(/[*_`]/g, '').trim())
    .filter(Boolean)
  for (const l of lines.slice(-4).reverse()) if (ASK_RE.test(l)) return l
  return null
}

export function labelOf(e: any): string {
  const s = String(e.description ?? e.command ?? e.prompt ?? e.file_path ?? e.pattern ?? '')
  return s.replace(/\s+/g, ' ').slice(0, 60)
}

const mmss = (ms: number) => {
  const s = Math.floor(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await update($, running, () => [])
    // Re-render to advance the elapsed time, only while a slow tool is running
    $.clock.every(1000, async () => {
      const list = await read($, running)
      if (!list.length) return
      const now = await $.clock.now()
      if (list.some(r => now - r.startedAt >= SLOW_MS)) $.ui.invalidate('ui.render')
    })
    return next(e)
  })

  on('prompt.submit', async ($, e, next) => {
    await update($, ask, () => null)
    return next(e)
  })

  on('tool.call', async ($, e, next) => {
    const id = String(e.tool_use_id ?? `${e.tool}-${Math.random().toString(36).slice(2, 8)}`)
    const row: Running = { id, tool: String(e.tool).replace(/^mcp__[^_]+__/, ''), label: labelOf(e), startedAt: await $.clock.now(), isAgent: !!e.agentId }
    await update($, running, list => [...list, row])
    try {
      return await next(e)
    } finally {
      await update($, running, list => list.filter(r => r.id !== id))
    }
  })

  on('turn.complete', async ($, e, next) => {
    if (!e.agentId) {
      await update($, ask, () => (e.isAborted ? null : askOf(e.answer ?? '')))
      const u = await $.session.usage().catch(() => undefined)
      await update($, level, () => levelOf(u?.context.percent))
    }
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    const { Box, Text, Button } = $.ui.resolve(e)
    const now = await $.clock.now()
    const width = Math.max(20, e.props.bodyColumns - 2)
    const rows: any[] = []

    const slow = (await read($, running)).filter(r => now - r.startedAt >= SLOW_MS)
    for (const r of slow.slice(-MAX_ROWS)) {
      rows.push(
        <Text key={`run-${r.id}`} wrap="truncate-end">
          <Text color="yellow">⏳ {mmss(now - r.startedAt)} </Text>
          <Text bold>{r.isAgent ? `↳${r.tool}` : r.tool}</Text>
          <Text dimColor> {r.label.slice(0, width)}</Text>
        </Text>,
      )
    }
    if (slow.length > MAX_ROWS) rows.push(<Text key="run-more" dimColor>  and {slow.length - MAX_ROWS} more</Text>)

    if (!e.props.isWorking) {
      const q = await read($, ask)
      if (q)
        rows.push(
          <Text key="ask" wrap="truncate-end">
            <Text color="cyan" bold>{APPROVE_KEY} </Text>
            <Text dimColor>approves: </Text>
            <Text>{q}</Text>
          </Text>,
        )
      const lv = await read($, level)
      if (lv && lv.action !== (await read($, hiddenLevel)))
        rows.push(
          <Box key="lv">
            <Text dimColor>ctx used {lv.pct}%, suggestion: </Text>
            <Text bold>{lv.action} </Text>
            <Button key="hide-lv" label="Close" plain onPress={() => update($, hiddenLevel, () => lv.action)} />
          </Box>,
        )
    }

    if (!rows.length) return next(e)
    const inner = await next(e)
    return (
      <Box flexDirection="column">
        {rows}
        {inner}
      </Box>
    )
  })
}
