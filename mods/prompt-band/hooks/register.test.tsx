import { test, expect } from 'claude-code/testing'
import { askOf, levelOf, labelOf } from './register'

const BAND = { component: 'AbovePrompt', props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 80, scroll: { offset: 0, bodyRows: 9 }, view: {} } } as any

test('picks the approval question from the end of a reply; plain statements give null', () => {
  expect(askOf('直しました。\n\nこの内容で下書きを作ってよいですか？')).toBe('この内容で下書きを作ってよいですか？')
  expect(askOf('結果です。\n- **どれから作りますか？**\n\n補足: スクリプトは保存済み。')).toBe('どれから作りますか？')
  expect(askOf('このままでよければ「OK」で、DMの下書きとして保存します（送信はしません）。')).toBe(
    'このままでよければ「OK」で、DMの下書きとして保存します（送信はしません）。',
  )
  expect(askOf('Fixed it.\n\nShall I create the draft with this text')).toBe('Shall I create the draft with this text')
  expect(askOf('Results below.\n- **Which one first?**\n\nNote: script saved.')).toBe('Which one first?')
  expect(askOf('完了しました。statusline に件名が出ます。')).toBeNull()
  expect(askOf('Done. The title now shows in the status line.')).toBeNull()
})

test('context hint thresholds', () => {
  expect(levelOf(undefined)).toBeNull()
  expect(levelOf(39)).toBeNull()
  expect(levelOf(40)!.action).toContain('memory')
  expect(levelOf(60)!.action).toContain('handoff')
  expect(levelOf(80)!.action).toContain('now')
})

test('tool label prefers the description and is truncated', () => {
  expect(labelOf({ description: 'replay the deny corpus', command: 'python x.py' })).toBe('replay the deny corpus')
  expect(labelOf({ command: 'a'.repeat(100) }).length).toBe(60)
})

test('band: with nothing to show, no rows of ours are rendered (works on every surface)', async ($, on) => {
  on('clock.now', () => ({ value: 0 }))
  on('ui.render', ($$, e) => {
    const { Box } = $$.ui.resolve(e)
    return <Box />
  })
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ plugin: 'prompt-band', surface, ...BAND })
    expect(await ui.find({ type: 'Text', text: /suggestion|approves|⏳/ })).toBeUndefined()
    await ui.unmount()
  }
})
