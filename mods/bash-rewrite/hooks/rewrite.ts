// Pure functions that rewrite Bash commands a PreToolUse guard denies into an equivalent form:
//   - inline backslashes: heredoc bodies or `python -c` code containing `\` (Git Bash on Windows mangles them)
//   - `cd` followed by a read command with a bare relative file name
// They do not touch $, so tests can call them directly.
//
// Policy: only rewrite forms that are certainly equivalent. If anything is doubtful, return null and let the
// original pass (the guard then stops it as before = fails safe).

export type Spill = { name: string; text: string }
export type Rewrite = { command: string; spills: Spill[]; kinds: string[] }

// ---------------------------------------------------------------- heredoc / python -c

const HEREDOC_START = /<<(-?)\s*(['"]?)([A-Za-z_]\w*)\2/
const PY_INLINE = /\bpython[0-9.]*\s+(?:-[^\s-]\S*\s+)*-c\b/

// Replace a heredoc whose body contains `\` with `< file`.
// Unquoted delimiters whose body contains `$` or `` ` `` rely on bash expansion, so those are left alone.
export function rewriteHeredocs(cmd: string, fileFor: (i: number) => string): Rewrite | null {
  const lines = cmd.split('\n')
  const out: string[] = []
  const spills: Spill[] = []
  let changed = false
  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const m = HEREDOC_START.exec(line)
    if (!m) {
      out.push(line)
      i++
      continue
    }
    const stripTabs = m[1] === '-'
    const quoted = m[2] !== ''
    const delim = m[3]
    const body: string[] = []
    let j = i + 1
    while (j < lines.length && lines[j].trim() !== delim) {
      body.push(stripTabs ? lines[j].replace(/^\t+/, '') : lines[j])
      j++
    }
    if (j >= lines.length) return null // no terminator = cannot interpret
    const text = body.join('\n')
    if (!text.includes('\\')) {
      // not something the guard targets; keep as-is
      out.push(...lines.slice(i, j + 1))
      i = j + 1
      continue
    }
    if (!quoted && /[$`]/.test(text)) return null
    // two heredocs on one line are not handled
    if (HEREDOC_START.test(line.slice(m.index + m[0].length))) return null
    const path = fileFor(spills.length)
    spills.push({ name: path, text: text + '\n' })
    out.push(line.slice(0, m.index) + '< ' + path + line.slice(m.index + m[0].length))
    changed = true
    i = j + 1
  }
  if (!changed) return null
  return { command: out.join('\n'), spills, kinds: spills.map(() => 'heredoc') }
}

// Replace `python ... -c <quoted>` with `python ... <file>`.
// The quoted content is written out "as the model wrote it" as Python source (bash does not get to halve it).
// Inside double quotes, only the bash-only escapes `\"` `\$` `` \` `` and line continuations are removed.
export function rewritePyInline(cmd: string, fileFor: (i: number) => string): Rewrite | null {
  if (!cmd.includes('\\') || !PY_INLINE.test(cmd)) return null
  const re = new RegExp(PY_INLINE.source, 'g')
  let out = ''
  let last = 0
  const spills: Spill[] = []
  let m: RegExpExecArray | null
  while ((m = re.exec(cmd))) {
    const cAt = m.index + m[0].length - 2 // position of `-c`
    let k = m.index + m[0].length
    while (cmd[k] === ' ' || cmd[k] === '\t') k++
    const q = cmd[k]
    if (q !== "'" && q !== '"') return null
    let code = ''
    let p = k + 1
    if (q === "'") {
      const end = cmd.indexOf("'", p)
      if (end < 0) return null
      code = cmd.slice(p, end)
      p = end + 1
    } else {
      let closed = false
      while (p < cmd.length) {
        const ch = cmd[p]
        if (ch === '\\' && p + 1 < cmd.length) {
          const nx = cmd[p + 1]
          if (nx === '"' || nx === '$' || nx === '`') code += nx
          else if (nx === '\n') code += ''
          else code += ch + nx
          p += 2
          continue
        }
        if (ch === '$' || ch === '`') return null // relies on expansion
        if (ch === '"') {
          closed = true
          p++
          break
        }
        code += ch
        p++
      }
      if (!closed) return null
    }
    // a word right after the quote ('..'"'"'..' concatenation etc.) is not handled
    if (p < cmd.length && !/[\s;&|)]/.test(cmd[p])) return null
    const path = fileFor(spills.length)
    spills.push({ name: path, text: code.endsWith('\n') ? code : code + '\n' })
    out += cmd.slice(last, cAt) + path
    last = p
    re.lastIndex = p
  }
  if (spills.length === 0) return null
  out += cmd.slice(last)
  if (PY_INLINE.test(out)) return null
  return { command: out, spills, kinds: spills.map(() => 'python -c') }
}

// ---------------------------------------------------------------- cd + relative path

type Word = { raw: string; value: string; start: number; end: number; dynamic: boolean; quoted: boolean }
type Tok = { op: string } | Word

const OPS = ['&&', '||', ';', '|', '&', '\n']

export function tokenize(cmd: string): Tok[] | null {
  const toks: Tok[] = []
  let i = 0
  while (i < cmd.length) {
    const ch = cmd[i]
    if (ch === ' ' || ch === '\t' || ch === '\r') {
      i++
      continue
    }
    if (ch === '\\' && cmd[i + 1] === '\n') {
      i += 2
      continue
    }
    const op = OPS.find(o => cmd.startsWith(o, i))
    if (op) {
      toks.push({ op })
      i += op.length
      continue
    }
    if (ch === '#') {
      while (i < cmd.length && cmd[i] !== '\n') i++
      continue
    }
    const start = i
    let value = ''
    let dynamic = false
    let quoted = false
    while (i < cmd.length) {
      const c = cmd[i]
      if (c === ' ' || c === '\t' || c === '\r' || OPS.some(o => cmd.startsWith(o, i))) break
      if (c === "'") {
        const end = cmd.indexOf("'", i + 1)
        if (end < 0) return null
        value += cmd.slice(i + 1, end)
        quoted = true
        i = end + 1
        continue
      }
      if (c === '"') {
        quoted = true
        i++
        let closed = false
        while (i < cmd.length) {
          const d = cmd[i]
          if (d === '\\' && i + 1 < cmd.length) {
            value += '"$`\\\n'.includes(cmd[i + 1]) ? cmd[i + 1] : d + cmd[i + 1]
            i += 2
            continue
          }
          if (d === '$' || d === '`') dynamic = true
          if (d === '"') {
            closed = true
            i++
            break
          }
          value += d
          i++
        }
        if (!closed) return null
        continue
      }
      if (c === '\\' && i + 1 < cmd.length) {
        value += cmd[i + 1]
        quoted = true
        i += 2
        continue
      }
      if (c === '$' || c === '`' || c === '(' || c === ')') dynamic = true
      value += c
      i++
    }
    toks.push({ raw: cmd.slice(start, i), value, start, end: i, dynamic, quoted })
  }
  return toks
}

const READER = /^(grep|rg|egrep|fgrep|cat|head|tail|less|wc|nl|sed|awk)\b/
const PATTERN_FIRST = new Set(['grep', 'rg', 'egrep', 'fgrep', 'sed', 'awk'])
const VALUE_FLAGS = new Set(['-e', '-f', '--include', '--exclude', '--glob', '-g', '--type', '-t', '-m', '-A', '-B', '-C', '--max-count'])

function looksRelativeFile(v: string): boolean {
  if (!v || v.startsWith('-') || v.includes('/') || v.includes('\\')) return false
  if (v.startsWith('$') || v.startsWith('`')) return false
  return v === '.' || /\.[A-Za-z0-9]{1,8}$/.test(v)
}

// Normalise Windows / ~ / POSIX paths to the Git Bash POSIX form (/c/Users/...)
export function toPosix(p: string, home: string): string | null {
  if (p === '~') return home
  if (p.startsWith('~/')) return home + p.slice(1)
  const w = /^([A-Za-z]):[\\/](.*)$/.exec(p)
  if (w) return '/' + w[1].toLowerCase() + '/' + w[2].replace(/\\/g, '/')
  if (p.startsWith('/')) return p
  return null
}

function join(dir: string, rel: string): string {
  const parts = dir.replace(/\/+$/, '').split('/')
  for (const seg of rel.split('/')) {
    if (seg === '' || seg === '.') continue
    if (seg === '..') parts.length > 2 && parts.pop()
    else parts.push(seg)
  }
  return parts.join('/') || '/'
}

const UNSAFE_DIR = /[\s'"$`\\*?[\]{}()<>|;&!#]/

// Turn bare file names passed to read commands after a `cd` into absolute paths.
// The cd itself is kept (so the meaning of other commands does not change). Targets are picked like the guard does.
export function rewriteCdRelative(cmd: string, cwd: string, home: string): Rewrite | null {
  if (!/(?:^|[;&|]|\s)(?:cd|pushd)\s/.test(cmd)) return null
  const toks = tokenize(cmd)
  if (!toks) return null

  const commands: Word[][] = []
  let cur: Word[] = []
  for (const t of toks) {
    if ('op' in t) {
      if (cur.length) commands.push(cur)
      cur = []
    } else cur.push(t)
  }
  if (cur.length) commands.push(cur)

  let dir: string | null = toPosix(cwd, home)
  const edits: { start: number; end: number; text: string }[] = []
  for (const words0 of commands) {
    const words = words0.filter(w => w.value !== 'sudo')
    if (!words.length) continue
    const head = words[0].value
    if (head === 'cd' || head === 'pushd') {
      const target = words[1]
      if (!target) dir = home
      else if (target.dynamic || target.value === '-') dir = null
      else {
        const abs = toPosix(target.value, home)
        dir = abs ?? (dir ? join(dir, target.value) : null)
      }
      continue
    }
    const m = READER.exec(head)
    if (!m) continue
    const prog = m[1]
    const rest = words.slice(1)
    if ((prog === 'sed' || prog === 'awk') && rest.some(w => w.value.startsWith('-i'))) continue
    let args: Word[] = []
    let skip = false
    for (const w of rest) {
      if (skip) {
        skip = false
        continue
      }
      if (VALUE_FLAGS.has(w.value)) {
        skip = true
        continue
      }
      if (w.value.startsWith('-')) continue
      args.push(w)
    }
    if (PATTERN_FIRST.has(prog) && args.length) args = args.slice(1)

    for (const w of args) {
      // for words with a redirect such as `>out.txt`, only the part after the symbol is a file name
      const redir = /^([0-9]*[<>]+&?)/.exec(w.raw)
      const prefix = redir ? redir[1] : ''
      const value = redir ? w.value.slice(prefix.length) : w.value
      if (!looksRelativeFile(value) || w.dynamic) continue
      if (!cmd.includes(value)) continue // same as the guard: words not present verbatim are not judged
      if (!dir || UNSAFE_DIR.test(dir)) return null
      const abs = value === '.' ? dir : dir.replace(/\/+$/, '') + '/' + value
      const rawBody = w.raw.slice(prefix.length)
      let text: string
      if (rawBody === value) text = prefix + abs
      else if ((rawBody[0] === '"' || rawBody[0] === "'") && rawBody.endsWith(rawBody[0]) && rawBody.slice(1, -1) === value)
        text = prefix + rawBody[0] + abs + rawBody[0]
      else return null
      edits.push({ start: w.start, end: w.end, text })
    }
  }
  if (!edits.length) return null
  let out = cmd
  for (const ed of edits.sort((a, b) => b.start - a.start)) out = out.slice(0, ed.start) + ed.text + out.slice(ed.end)
  return { command: out, spills: [], kinds: edits.map(() => 'cd+relative') }
}
