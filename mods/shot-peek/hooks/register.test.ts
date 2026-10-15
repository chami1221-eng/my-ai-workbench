import { test, expect } from 'claude-code/testing'
import { labelOf } from './register'

test('the date/time in the file name becomes "MM/DD HH:MM:SS" (English and Japanese Windows names)', () => {
  expect(labelOf('D:\\home\\x\\Pictures\\Screenshots\\Screenshot 2026-10-03 125049.png')).toBe('10/03 12:50:49')
  expect(labelOf('D:\\home\\x\\Pictures\\Screenshots\\スクリーンショット 2026-10-03 125049.png')).toBe('10/03 12:50:49')
})

test('names without a date/time are shown as-is', () => {
  expect(labelOf('/home/x/Pictures/Screenshots/foo.png')).toBe('foo.png')
})
