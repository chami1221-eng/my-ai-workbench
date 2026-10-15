# -*- coding: utf-8 -*-
"""Drop-in for `runpy.run_path(path, run_name='__main__')` that caches compiled code.

runpy.run_path ignores .pyc and compiles the source on every call. The dispatcher runs
several checks per tool call, so this adds up.

Safety:
  - execution goes through the same `runpy._run_module_code` with the same arguments
    (__name__='__main__', __file__, sys.argv[0], sys.modules['__main__'] swap all identical)
  - the cache is used only when Python MAGIC + mtime_ns + size all match; otherwise recompile
  - any failure (cache I/O, private API missing) falls back to plain runpy.run_path:
    slower, never a different verdict
  - writes go tmp -> os.replace, so parallel sessions never read a half-written file
"""
import importlib.util
import marshal
import os
import runpy

CACHE_DIR = os.path.join(os.path.expanduser('~'), '.claude', '.cache', 'hook_codecache')
_TAG = importlib.util.MAGIC_NUMBER.hex().encode('ascii')


def _cache_file(path):
    # No hashlib: importing it (OpenSSL) costs about as much as the compile we are skipping.
    p = os.path.abspath(path).lower()
    safe = ''.join(c if c.isalnum() or c in '-_.' else '_' for c in p)
    return os.path.join(CACHE_DIR, safe[-150:] + '.bin')


def load_code(path):
    st = os.stat(path)
    head = _TAG + b'|%d|%d' % (st.st_mtime_ns, st.st_size)
    cf = _cache_file(path)
    try:
        with open(cf, 'rb') as f:
            data = f.read()
        h, _, body = data.partition(b'\n')
        if h == head:
            return marshal.loads(body)
    except Exception:
        pass
    with open(path, 'rb') as f:
        src = f.read()
    code = compile(src, path, 'exec')
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = '%s.%d.tmp' % (cf, os.getpid())
        with open(tmp, 'wb') as f:
            f.write(head + b'\n' + marshal.dumps(code))
        os.replace(tmp, cf)
    except Exception:
        pass
    return code


def run_path_cached(path):
    runner = getattr(runpy, '_run_module_code', None)
    try:
        if runner is None:
            raise RuntimeError('runpy._run_module_code missing')
        code = load_code(path)
    except Exception:
        return runpy.run_path(path, run_name='__main__')
    return runner(code, None, '__main__', pkg_name='', script_name=path)
