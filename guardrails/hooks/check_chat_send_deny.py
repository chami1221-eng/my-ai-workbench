# -*- coding: utf-8 -*-
"""Deny immediate chat sends (e.g. Slack) at PreToolUse. Drafts pass; scheduled sends pass with a warning.

Why / risk: a rule "show the full text -> user says OK -> send" that lives only in the
prompt invites the agent to offer "shall I send it?", and the tool permission prompt
does not display the message body, so a Yes/No click there is not a real approval of
the text. This hook keeps the send decision on the human side: the agent can only draft.

- deny : CHAT_SEND_TOOLS      (immediate send)
- warn : CHAT_SCHEDULE_TOOLS  (scheduled send; it really fires later, so the
                               show-text-then-OK step must still happen first)
- pass : CHAT_DRAFT_TOOL      (draft)

If you ever relax this, do it explicitly and visibly. Silently removing the part
from the dispatcher brings back silent sends.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard_config as C  # noqa: E402

DENY_REASON = (
    '[rule=chat-send] Immediate chat sends are not allowed from the agent.\n'
    'Use ' + C.CHAT_DRAFT_TOOL + ' to create a draft, report "draft created", and stop. '
    'The human presses Send in the chat app.\n'
    'Do not offer "OK to send?": that hands the decision back without showing the body.\n'
    'For a timed send, a scheduled-send tool is available.'
)

SCHEDULE_NOTE = (
    'Scheduled sends really go out later. Do not skip the steps:\n'
    '  1. show the full text in the conversation  2. get the user\'s OK  3. call this tool\n'
    'If 1 and 2 have not happened, do not call it now; show the text first.\n'
    '(The permission prompt does not show the body, so its Yes/No is not an approval.)'
)


def main():
    try:
        payload = json.loads(sys.stdin.read() or '{}')
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    tool = payload.get('tool_name') or ''

    if tool in C.CHAT_SEND_TOOLS:
        hso = {'permissionDecision': 'deny', 'permissionDecisionReason': DENY_REASON}
    elif tool in C.CHAT_SCHEDULE_TOOLS:
        hso = {'additionalContext': SCHEDULE_NOTE}
    else:
        return
    hso['hookEventName'] = 'PreToolUse'
    print(json.dumps({'hookSpecificOutput': hso}, ensure_ascii=False))


if __name__ == '__main__':
    main()
