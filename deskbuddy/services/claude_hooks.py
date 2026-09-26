"""Claude Code hooks → Buddy messages.

Claude Code runs a command on its events and passes a JSON object on stdin
(hook_event_name, session_id, cwd, and for Notification a message). We use three:

  UserPromptSubmit  you sent a prompt: remember when, per session
  Stop              Claude finished answering: tell Buddy if it took LONG_TASK or more
  Notification      Claude needs your permission or input: always tell Buddy

Start times live in small files under the runtime dir, one per session."""

import json
import os
import time
from pathlib import Path

LONG_TASK = int(os.environ.get("BUDDY_CLAUDE_LONG_TASK", 60))       # seconds
STATE = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/desktop-buddy-{os.getuid()}") / "desktop-buddy-claude"


def _state_file(state_dir, session):
    safe = "".join(c for c in str(session) if c.isalnum() or c in "-_")[:80] or "default"
    return Path(state_dir) / safe


def handle(event, state_dir=STATE, now=None):
    """The Buddy message for a hook event, or None."""
    now = time.time() if now is None else now
    name = event.get("hook_event_name", "")
    project = Path(event.get("cwd") or ".").name or "your project"
    marker = _state_file(state_dir, event.get("session_id", "default"))

    if name == "UserPromptSubmit":
        Path(state_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        marker.write_text(str(now))
        return None
    if name == "Stop":
        try:
            started = float(marker.read_text())
        except (OSError, ValueError):
            return None                                # we didn't see it start
        marker.unlink(missing_ok=True)
        seconds = now - started
        if seconds < LONG_TASK:
            return None
        return {"cmd": "claude", "kind": "done", "project": project, "seconds": round(seconds)}
    if name == "Notification":
        message = str(event.get("message") or "Claude Code is waiting for you")
        return {"cmd": "claude", "kind": "attention", "project": project, "message": message[:200]}
    return None


def describe(message):
    """(title, body) for a desktop notification / speech bubble."""
    if message["kind"] == "done":
        minutes = max(1, round(message.get("seconds", 60) / 60))
        return "✅ Claude Code finished", f"{message['project']}: done after {minutes} min"
    return "⚠️ Claude Code needs you", f"{message['project']}: {message.get('message', '')}"


def run_hook(stdin_text, send):
    """Entry point for the hook command: never fails, never blocks Claude Code."""
    try:
        event = json.loads(stdin_text or "{}")
        message = handle(event) if isinstance(event, dict) else None
        if message:
            send(message)
    except Exception:          # a broken hook must not get in Claude Code's way
        pass
