# -*- coding: utf-8 -*-
"""Shared configuration for the guardrail hooks.

Every value here has a generic default and can be overridden with an environment
variable, so you can adapt the guards without editing the check scripts.
List-valued env vars use os.pathsep (";" on Windows, ":" elsewhere) for paths and
"," for names.
"""
import glob
import os
from pathlib import Path

HOME = Path.home()


def _env_paths(name, default):
    raw = os.environ.get(name, '')
    if raw.strip():
        return [Path(os.path.expanduser(p)) for p in raw.split(os.pathsep) if p.strip()]
    return list(default)


def _env_names(name, default):
    raw = os.environ.get(name, '')
    if raw.strip():
        return tuple(x.strip() for x in raw.split(',') if x.strip())
    return tuple(default)


# --------------------------------------------------------------- output-root guard
# Agents love to drop files straight into the first directory they know about.
# Writes directly under PROTECTED_ROOT are denied (Write/Edit) or asked (Bash),
# unless the first path component is in PROTECTED_ROOT_ALLOWED.
PROTECTED_ROOT = Path(os.path.expanduser(
    os.environ.get('GUARD_PROTECTED_ROOT', str(HOME / 'Documents'))))
PROTECTED_ROOT_ALLOWED = _env_names(
    'GUARD_PROTECTED_ROOT_ALLOWED',
    ('_output', '.claude', 'CLAUDE.md', 'AGENTS.md', 'memory', 'projects'))
# Where a scratch script should go instead of a heredoc (shown in deny messages only)
SCRATCH_DIR_HINT = os.environ.get('GUARD_SCRATCH_DIR_HINT', '~/Documents/_output/scripts/')

# --------------------------------------------------------------- instruction files
# Files whose edits should stop for a human click ("ask"), e.g. a CLAUDE.md whose
# shared rules were moved to a canonical AGENTS.md read by several agents.
ASK_EDIT_FILES = _env_paths('GUARD_ASK_EDIT_FILES', [HOME / '.claude' / 'CLAUDE.md'])
CANONICAL_RULES_FILE = os.environ.get('GUARD_CANONICAL_RULES_FILE', '~/AGENTS.md')

# --------------------------------------------------------------- memory value drop
# Directories holding long-lived agent memory notes (Markdown).
MEMORY_DIRS = _env_paths(
    'GUARD_MEMORY_DIRS',
    [Path(p) for p in glob.glob(str(HOME / '.claude' / 'projects' / '*' / 'memory'))])
# Only these note files are protected (index files are meant to be short and are excluded).
MEMORY_PREFIXES = _env_names('GUARD_MEMORY_PREFIXES', ('project_', 'feedback_', 'reference_'))
# Markers that record that an old value was deliberately superseded.
INVALIDATION_MARKS = ('[INVALIDATED', '[ERRATUM', '【無効', '【誤記')
# Record IDs in your system of record. Default matches ticket-style IDs like DEAL-1234.
RECORD_ID_RE = os.environ.get('GUARD_RECORD_ID_RE', r'\b[A-Z]{2,6}-\d{3,}\b')

# --------------------------------------------------------------- chat send
CHAT_SEND_TOOLS = _env_names('GUARD_CHAT_SEND_TOOLS', ('mcp__slack__slack_send_message',))
CHAT_SCHEDULE_TOOLS = _env_names('GUARD_CHAT_SCHEDULE_TOOLS', ('mcp__slack__slack_schedule_message',))
CHAT_DRAFT_TOOL = os.environ.get('GUARD_CHAT_DRAFT_TOOL', 'mcp__slack__slack_send_message_draft')

# --------------------------------------------------------------- web route
SEARCH_CMD = os.environ.get('GUARD_SEARCH_CMD', 'python ~/.claude/scripts/searxng-search.py "query"')
# Built-in WebSearch is unavailable on some model providers. Set GUARD_NO_WEBSEARCH=1,
# or list env vars (comma separated) whose presence means "this provider has no WebSearch".
NO_WEBSEARCH_ENV_VARS = _env_names('GUARD_NO_WEBSEARCH_ENV_VARS', ())

# --------------------------------------------------------------- agent fan-out
RESEARCH_AGENTS = set(_env_names(
    'GUARD_RESEARCH_AGENTS', ('web-researcher', 'notes-researcher', 'db-researcher', 'kb-researcher')))
GENERIC_AGENTS = set(_env_names('GUARD_GENERIC_AGENTS', ('general-purpose', 'Explore', 'claude')))

# --------------------------------------------------------------- bash: irreversible actions
# Business actions that only a human may perform. The agent may prepare everything
# up to that point, but the final click happens in the business app's UI.
# Example: closing a CRM deal as lost. Replace with your own fields/values.
IRREVERSIBLE_ACTION_RULES = [
    {
        'rule': 'crm-close-lost',
        'patterns': [
            r"\bStage(?:Name)?\s*=\s*['\"]?\s*(?:\d+\s*-\s*)?(?:Closed\s*Lost|Dead)",
            r"['\"]Stage(?:Name)?['\"]\s*:\s*['\"]\s*(?:\d+\s*-\s*)?(?:Closed\s*Lost|Dead)",
        ],
        'reason': (
            'Setting a deal to Closed Lost / Dead is reserved for a human. '
            'Prepare the record (reason fields, notes, linked quotes check) and hand over '
            'the record URL with a one-line instruction; the owner clicks the final stage in the UI. '
            'Risk: a stage change that cannot be undone, e.g. on a deal that still has an approved quote attached.'
        ),
    },
]

# --------------------------------------------------------------- bash: per-segment rules
# deny  : regex that triggers the rule
# allow : regex that exempts the rule, evaluated on the SAME command segment with
#         trailing comments stripped (a "# helper.py" comment must not exempt anything)
SEGMENT_RULES = [
    {
        'rule': 'public-post',
        'deny': r'\b(CreateTweet|postTweet|publishTweet|sendTweet)\b',
        'allow': r'\bpost-cli(?:\.js)?\s+draft\s',
        'skip_search_only': False,
        'reason': ('Public posting mutations are draft-only. Use `post-cli draft "..."` and let '
                   'the human publish.'),
    },
    {
        'rule': 'direct-api',
        'deny': r'https?://api\.example\.com/',
        'allow': r'\bapi_client\.py\b',
        'skip_search_only': True,
        'reason': ('Call this API through api_client.py (auth, retries, audit log). '
                   'Hand-rolled curl/urllib calls skip those.'),
    },
]
