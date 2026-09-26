"""Talking to the running Buddy from other programs: a local socket, one JSON object per line.

    {"cmd": "say", "text": "Build finished", "speak": false}
    {"cmd": "claude", "kind": "done" | "attention", "project": "desktop-buddy", ...}
    {"cmd": "open", "what": "firefox"} -> {"ok": true, "message": "Opening Firefox."}
    {"cmd": "status"}  -> replies with one JSON line

The socket lives in $XDG_RUNTIME_DIR (a per-user, owner-only directory), so only your own
programs can reach it. The client side uses the plain socket module, not Qt, so the
`buddy` command and the Claude Code hook start fast and never hang: if Buddy isn't
running they just give up."""

import json
import os
import socket
from pathlib import Path

SOCKET = Path(os.environ.get("BUDDY_SOCKET") or
              Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/desktop-buddy-{os.getuid()}") / "desktop-buddy.sock")
MAX_MESSAGE = 16_000


def send(message, timeout=0.5, want_reply=False, path=None):
    """Send one message to Buddy. Returns the reply (dict) if asked, True if delivered,
    None if Buddy isn't running."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(path or SOCKET))
            s.sendall(json.dumps(message).encode()[:MAX_MESSAGE] + b"\n")
            if not want_reply:
                return True
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            return json.loads(data) if data else None
    except (OSError, ValueError):
        return None


def parse(line):
    """A message from a client, or None if it isn't a JSON object with a cmd."""
    try:
        message = json.loads(line)
    except ValueError:
        return None
    return message if isinstance(message, dict) and isinstance(message.get("cmd"), str) else None
