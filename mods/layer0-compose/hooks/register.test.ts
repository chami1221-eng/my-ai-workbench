import { test, expect } from 'claude-code/testing'
import { strip, hookChunk, isStartupLoad, expandHome } from './register'

test('only a load right after startup may strip; mid-session loads and unknown timing do not', () => {
  expect(isStartupLoad(1_000_000 + 5_000, 1_000_000)).toBe(true)
  expect(isStartupLoad(1_000_000 + 600_000, 1_000_000)).toBe(false) // hot reload
  expect(isStartupLoad(1_000_000, Number.NaN)).toBe(false)
  expect(isStartupLoad(999_000, 1_000_000)).toBe(false)
})

const L0 = '- rule A\n- rule B\n'
const CH = hookChunk(L0)

test('an injection that is only Layer 0 is removed entirely', () => {
  expect(strip([CH], L0)).toEqual([])
})

test('a leading Layer 0 is removed and trailing context (shortcuts, memory notices) stays', () => {
  expect(strip([CH + '\n\n📌 memory notice'], L0)).toEqual(['📌 memory notice'])
  expect(strip(['preface\n\n' + CH + '\n\ntail'], L0)).toEqual(['preface\n\ntail'])
})

test('no exact match means nothing is removed (twice is safer than zero)', () => {
  expect(strip([hookChunk('- rule A (edited)\n')], L0)).toBeNull()
  expect(strip(['📌 memory notice only'], L0)).toBeNull()
})

test('~ is expanded from the home directory', () => {
  expect(expandHome('~/.claude/layer0.md', '/home/u')).toBe('/home/u/.claude/layer0.md')
  expect(expandHome('/etc/layer0.md', '/home/u')).toBe('/etc/layer0.md')
})
