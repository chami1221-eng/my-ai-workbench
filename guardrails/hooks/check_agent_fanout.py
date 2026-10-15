"""PreToolUse (Agent): warn when one session starts spawning several subagents. Never blocks.

Why: many parallel subagents multiply cost and contend for the same resources, so the
warning starts from the second one. The rule "delegate heavy multi-source research to a
single multi-agent process, or run it sequentially" can live in the instructions, but a
skill's own playbook may not mention it, and then the gate is easy to skip. Instructions
can be skipped; a hook fires every time.

Behavior (count is per session, research + generic combined):
  research agent, 1st   -> light note (one source is fine, but decide now)
  generic agent, 1st    -> silent (counted only; avoids nagging on "find one file")
  2nd and later         -> strong warning
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard_config as C  # noqa: E402

COUNTED_AGENTS = C.RESEARCH_AGENTS | C.GENERIC_AGENTS
STATE_DIR = os.path.join(os.path.expanduser("~"), ".claude", ".agent_fanout")
TTL_SEC = 24 * 60 * 60


def _load_count(session_id):
    path = os.path.join(STATE_DIR, f"{session_id}.json")
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if time.time() - d.get("ts", 0) > TTL_SEC:
            return 0
        return int(d.get("count", 0))
    except Exception:
        return 0


def _save_count(session_id, count):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(os.path.join(STATE_DIR, f"{session_id}.json"), "w", encoding="utf-8") as f:
            json.dump({"count": count, "ts": time.time()}, f)
    except Exception:
        pass


def _gc():
    try:
        now = time.time()
        for name in os.listdir(STATE_DIR):
            p = os.path.join(STATE_DIR, name)
            if now - os.path.getmtime(p) > TTL_SEC:
                os.remove(p)
    except Exception:
        pass


def main():
    try:
        inp = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if not isinstance(inp, dict) or inp.get("tool_name", "") != "Agent":
        sys.exit(0)

    ti = inp.get("tool_input", {}) or {}
    subagent = (ti.get("subagent_type") or "").strip() or "general-purpose"
    if subagent not in COUNTED_AGENTS:
        sys.exit(0)

    session_id = "".join(c if c.isalnum() or c in "-_" else "_"
                         for c in str(inp.get("session_id") or "nosession"))[:64]
    _gc()
    count = _load_count(session_id) + 1
    _save_count(session_id, count)

    if count == 1 and subagent in C.GENERIC_AGENTS:
        sys.exit(0)

    if count == 1:
        msg = (f"Starting research subagent '{subagent}'. If you plan to run 2 or more in this "
               "session, decide now: parallel subagents multiply cost and contend for the same "
               "resources. One agent is fine as is.")
    else:
        msg = (f"Subagent #{count} in this session ('{subagent}'). Running several in parallel "
               "here is not the default route. Delegate the remaining work to a single "
               "multi-agent process or run it sequentially. "
               "You can still move the remaining agents now.")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "additionalContext": msg,
    }}, ensure_ascii=False))


if __name__ == "__main__":
    main()
