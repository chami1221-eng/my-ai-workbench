import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { Shot } from '../types'

// When Claude Reads an image from the Screenshots folder (e.g. after "read my latest screenshot"), show one line
// above the prompt saying which screenshot it read, so the user can confirm it was the intended one.
// Read itself is never changed (display only). The line clears on the next submit.
//
// A thumbnail (Image element) could not be drawn in Windows Terminal (tried { file } and base64 { png }),
// so only the capture time (taken from the file name) is shown. On a terminal that renders images, Image could return.

// ---- Config ------------------------------------------------------------------
// Which reads count as "a screenshot". Default: any .png under a Pictures/Screenshots folder (Windows default).
const SHOT = /[\\/]Pictures[\\/]Screenshots[\\/][^\\/]+\.png$/i
const MAX = 3 // how many recent screenshots to list
// ------------------------------------------------------------------------------

const shots = atom({ plugin: 'shot-peek', key: 'shots' } as const, [] as Shot[])
const isHidden = atom({ plugin: 'shot-peek', key: 'isHidden' } as const, false)

// "Screenshot 2026-10-03 125049.png" (any locale prefix) -> "10/03 12:50:49"
export function labelOf(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? path
  const m = name.match(/(\d{4})-(\d{2})-(\d{2})\s+(\d{2})(\d{2})(\d{2})/)
  return m ? `${m[2]}/${m[3]} ${m[4]}:${m[5]}:${m[6]}` : name
}

export const register: Register = on => {
  on('prompt.submit', async ($, e, next) => {
    await update($, shots, () => [])
    await update($, isHidden, () => false)
    return next(e)
  })

  on('tool.call', { tool: 'Read' }, async ($, e, next) => {
    const ran = await next(e)
    if (e.agentId || !SHOT.test(e.file_path) || ran.deny !== undefined || ran.isError) return ran
    const shot: Shot = { path: e.file_path, label: labelOf(e.file_path) }
    await update($, shots, list => [...list.filter(s => s.path !== shot.path), shot].slice(-MAX))
    return ran
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const list = await read($, shots)
    if (e.props.hasSurvey || list.length === 0 || (await read($, isHidden))) return next(e)

    const { Box, Text, Button } = $.ui.resolve(e)

    return (
      <Box>
        <Text>📷 Screenshots read: </Text>
        <Text bold>{list.map((s, i) => `${i + 1}. ${s.label}`).join(' / ')}</Text>
        <Text dimColor> (clears on next submit) </Text>
        <Button key="hide" label="Close" onPress={() => update($, isHidden, () => true)} />
      </Box>
    )
  })
}
