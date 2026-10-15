# -*- coding: utf-8 -*-
"""PreToolUse: keep web search and web fetch on routes that actually work.

Risks it addresses:
  - WebFetch pointed at a search engine results page hits a CAPTCHA.
  - Built-in WebSearch is unavailable on some model providers (the call fails), so the
    default search path is a local SearXNG instance. If SearXNG returns 0 hits, an agent
    could fall back to WebSearch anyway and burn a round trip on an error.

Behavior:
  - WebSearch on a provider without it        -> deny (it can only fail)
  - WebSearch otherwise                       -> light note (default is SearXNG)
  - WebFetch on a search-engine query URL     -> strong warning (not blocked)
  - WebFetch on a normal page                 -> light note (prefer the scraping fetcher first)

Provider detection reads this process's environment: hooks are started by Claude Code
directly, so they see its env, not the user's interactive shell profile.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard_config as C  # noqa: E402

SEARCH_HOSTS = (
    'duckduckgo.com', 'bing.com', 'google.com/search', 'google.co.jp/search',
    'search.yahoo.co.jp', 'baidu.com/s', 'lite.duckduckgo.com', 'html.duckduckgo.com',
)

WEBSEARCH_DENY = (
    '[rule=websearch-unavailable] WebSearch does not work on this model provider ({why}).\n'
    'Search with: ' + C.SEARCH_CMD + '\n'
    'If SearXNG returns 0 hits, do not fall back to WebSearch: wait 30-60 s and retry, '
    'restrict to one upstream engine, or fetch a known primary site directly.'
)
WEBSEARCH_MSG = 'Default search path is SearXNG (' + C.SEARCH_CMD + ').'
SEARCH_MSG = (
    'WebFetch is being pointed at a search engine. That does not work (CAPTCHA or '
    'unrelated results; stealth fetchers hit the same CAPTCHA).\n'
    'Route: 1) ' + C.SEARCH_CMD + ' to get URLs  2) fetch those URLs with the scraping '
    'fetcher  3) WebFetch only if the fetcher fails in every mode.\n'
    'Do not look for ways to scrape search engines.'
)
FETCH_MSG = ('First choice for fetching pages is the scraping fetcher (basic -> stealth -> '
             'max-stealth). WebFetch is the fallback. If you already tried it, continue.')


def no_websearch_reason():
    env = os.environ
    if env.get('GUARD_NO_WEBSEARCH', '').strip() not in ('', '0'):
        return 'GUARD_NO_WEBSEARCH is set'
    for var in C.NO_WEBSEARCH_ENV_VARS:
        if env.get(var, '').strip() not in ('', '0'):
            return '%s is set' % var
    return None


def emit(hso):
    hso = dict(hso, hookEventName='PreToolUse')
    print(json.dumps({'hookSpecificOutput': hso}, ensure_ascii=False))


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    tool = payload.get('tool_name') or ''

    if tool == 'WebSearch':
        why = no_websearch_reason()
        if why:
            emit({'permissionDecision': 'deny',
                  'permissionDecisionReason': WEBSEARCH_DENY.format(why=why)})
        else:
            emit({'additionalContext': WEBSEARCH_MSG})
        return
    if tool != 'WebFetch':
        return
    url = str((payload.get('tool_input') or {}).get('url') or '')
    low = url.lower()
    emit({'additionalContext': SEARCH_MSG if any(h in low for h in SEARCH_HOSTS) else FETCH_MSG})


if __name__ == '__main__':
    main()
