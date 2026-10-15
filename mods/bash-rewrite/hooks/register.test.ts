import { test, expect } from 'claude-code/testing'
import { rewriteCdRelative, rewriteHeredocs, rewritePyInline } from './rewrite'

const F = (i: number) => `/c/tmp/x_${i}.py`
const HOME = '/c/Users/u'
const BS = '\\'

test('heredoc: a body containing \\ becomes < file, and the body is kept as written', () => {
  const cmd = `python - <<'EOF'\nprint('C:${BS}${BS}Users${BS}${BS}2026')\nEOF\necho done`
  const r = rewriteHeredocs(cmd, F)!
  expect(r.command).toBe(`python - < /c/tmp/x_0.py\necho done`)
  expect(r.spills[0].text).toBe(`print('C:${BS}${BS}Users${BS}${BS}2026')\n`)
})

test('heredoc: a trailing pipe and cat > target are rewritten the same way', () => {
  expect(rewriteHeredocs(`python3 - <<EOF | head -5\nx='a${BS}b'\nEOF`, F)!.command).toBe('python3 - < /c/tmp/x_0.py | head -5')
  expect(rewriteHeredocs(`cat > /c/a.py <<'PY'\np='${BS}d'\nPY`, F)!.command).toBe('cat > /c/a.py < /c/tmp/x_0.py')
})

test('heredoc: forms left alone (no \\, unquoted with $, no terminator)', () => {
  expect(rewriteHeredocs(`python - <<'EOF'\nprint(1)\nEOF`, F)).toBeNull()
  expect(rewriteHeredocs(`python - <<EOF\nprint("$HOME${BS}x")\nEOF`, F)).toBeNull()
  expect(rewriteHeredocs(`python - <<'EOF'\nprint('${BS}n')`, F)).toBeNull()
})

test('python -c: single quotes keep content as-is, double quotes drop only \\"', () => {
  const r = rewritePyInline(`python -c 'print("a${BS}${BS}b")' arg1`, F)!
  expect(r.command).toBe('python /c/tmp/x_0.py arg1')
  expect(r.spills[0].text).toBe(`print("a${BS}${BS}b")\n`)
  const r2 = rewritePyInline(`cd /c/x && python -X utf8 -c "print(${BS}"C:${BS}${BS}x${BS}")" | head`, F)
  expect(r2).toBeNull() // -X utf8 (flag with a value) is not matched by the guard either, so leave it
  const r3 = rewritePyInline(`python -c "print(${BS}"C:${BS}${BS}x${BS}")" | head`, F)!
  expect(r3.command).toBe('python /c/tmp/x_0.py | head')
  expect(r3.spills[0].text).toBe(`print("C:${BS}${BS}x")\n`)
})

test('python -c: forms that rely on expansion or concatenated quotes are left alone', () => {
  expect(rewritePyInline(`python -c "import os; print('$HOME')" C:${BS}x`, F)).toBeNull()
  expect(rewritePyInline(`python -c 'a'"'"'b' C:${BS}x`, F)).toBeNull()
  expect(rewritePyInline(`python script.py C:${BS}x`, F)).toBeNull()
})

test('cd+relative: only file names of read commands become absolute; the cd stays', () => {
  const r = rewriteCdRelative('cd ~/.claude/memory && grep -n "foo|bar" a.md b.md | head -3', 'C:\\Users\\u\\Documents', HOME)!
  expect(r.command).toBe('cd ~/.claude/memory && grep -n "foo|bar" /c/Users/u/.claude/memory/a.md /c/Users/u/.claude/memory/b.md | head -3')
  expect(rewriteCdRelative('cd /c/x && ls && cat *.jsonl', 'C:\\w', HOME)!.command).toBe('cd /c/x && ls && cat /c/x/*.jsonl')
  expect(rewriteCdRelative('cd /c/x && grep -rl foo .', 'C:\\w', HOME)!.command).toBe('cd /c/x && grep -rl foo /c/x')
  expect(rewriteCdRelative('cd /c/x && cd sub && head "a.txt"', 'C:\\w', HOME)!.command).toBe('cd /c/x && cd sub && head "/c/x/sub/a.txt"')
})

test('cd+relative: out of scope (not a reader, pattern argument, sed -i, variable cd target) is left alone', () => {
  expect(rewriteCdRelative('cd /c/x && python run.py', 'C:\\w', HOME)).toBeNull()
  expect(rewriteCdRelative('cd /c/x && grep foo.md /c/y/z.md', 'C:\\w', HOME)).toBeNull()
  expect(rewriteCdRelative("cd /c/x && sed -i 's/a/b/' a.md", 'C:\\w', HOME)).toBeNull()
  expect(rewriteCdRelative('cd "$D" && cat a.md', 'C:\\w', HOME)).toBeNull()
  expect(rewriteCdRelative('cd "/c/my dir" && cat a.md', 'C:\\w', HOME)).toBeNull()
})

test('tool.call: the rewritten command is passed down with a note for the model; nothing to rewrite = unchanged', async ($, on) => {
  const seen: string[] = []
  const store: Record<string, unknown> = {}
  on('clock.now', () => ({ value: 0 }))
  on('env.get', () => ({ value: 'C:\\Users\\u' }))
  on('session.cwd', () => ({ value: 'C:\\w' }))
  on('store.get', (_$, e) => ({ value: store[e.key] }))
  on('store.set', (_$, e) => ((store[e.key] = e.value), { value: undefined }))
  on('tool.call', { tool: 'Bash' }, (_$, e: any) => {
    seen.push(e.command)
    return { ref: 1, result: { stdout: 'ok', stderr: '', interrupted: false }, text: 'ok' } as any
  })
  const r: any = await $.tool.call({ tool: 'Bash', command: 'cd /c/x && cat a.md' } as any)
  expect(seen[0]).toBe('cd /c/x && cat /c/x/a.md')
  expect(r.context.join('')).toContain('bash-rewrite')
  await $.tool.call({ tool: 'Bash', command: 'ls -la' } as any)
  expect(seen[1]).toBe('ls -la')
})
