"""Record the screen (or one window, e.g. Teams) during a meeting, as a separate process:

    python -m deskbuddy.services.screen <output.mkv>

Wayland doesn't let apps read the screen directly; they ask the ScreenCast portal, which
shows GNOME's "choose what to share" dialog, then hands over a PipeWire stream. The choice
is remembered (restore token), so later meetings usually don't ask again. GStreamer
encodes it with the Intel GPU (vah264lpenc) at 5 frames a second, which is plenty to see
who is speaking and costs almost no CPU; about 400 MB per hour at 1080p.

Prints JSON lines: {"state": "asking"}, {"state": "recording", "at": <unix time>},
{"error": "..."}. SIGINT/SIGTERM stops it and finalizes the file."""

import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

FPS = 5
BITRATE = 1000                 # kbit/s
TOKEN_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "desktop-buddy" / "screencast_token"

MONITOR, WINDOW = 1, 2
CURSOR_EMBEDDED = 2
PERSIST_UNTIL_REVOKED = 2


def say(**msg):
    print(json.dumps(msg), flush=True)


def encoder_chain():
    """Hardware H.264 when the GPU has it, else the software encoder."""
    have_va = subprocess.run(["gst-inspect-1.0", "vah264lpenc"], capture_output=True).returncode == 0
    if have_va:
        return ["vapostproc", "!", "video/x-raw(memory:VAMemory),format=NV12", "!",
                "vah264lpenc", f"bitrate={BITRATE}"]
    return ["openh264enc", f"bitrate={BITRATE * 1000}", "complexity=low"]


def pipeline(node_id, out, fd):
    """gst-launch arguments; fd is the portal's PipeWire connection, inherited by gst-launch."""
    return ["gst-launch-1.0", "-e", "pipewiresrc", f"fd={fd}", f"path={node_id}", "do-timestamp=true",
            "keepalive-time=1000", "!", "videorate", "!", f"video/x-raw,framerate={FPS}/1", "!",
            "videoconvert", "!", "videoscale", "add-borders=true", "!",
            "video/x-raw,width=1920,height=1080,pixel-aspect-ratio=1/1", "!",
            *encoder_chain(), "!", "h264parse", "!", "matroskamux", "!", "filesink", f"location={out}"]


class Portal:
    """The ScreenCast portal over D-Bus (jeepney, blocking)."""

    def __init__(self):
        from jeepney import DBusAddress
        from jeepney.io.blocking import open_dbus_connection
        self.conn = open_dbus_connection(bus="SESSION", enable_fds=True)
        self.addr = DBusAddress("/org/freedesktop/portal/desktop", bus_name="org.freedesktop.portal.Desktop",
                                interface="org.freedesktop.portal.ScreenCast")
        self.sender = self.conn.unique_name[1:].replace(".", "_")

    def _request(self, method, signature, *args, timeout=300):
        """Call a portal method that answers with a Request.Response signal."""
        from jeepney import MatchRule, message_bus, new_method_call
        token = "buddy" + secrets.token_hex(6)
        options = dict(args[-1], handle_token=("s", token))
        path = f"/org/freedesktop/portal/desktop/request/{self.sender}/{token}"
        rule = MatchRule(type="signal", interface="org.freedesktop.portal.Request", member="Response", path=path)
        self.conn.send_and_get_reply(new_method_call(message_bus, "AddMatch", "s", (rule.serialise(),)))
        with self.conn.filter(rule) as queue:
            self.conn.send_and_get_reply(new_method_call(self.addr, method, signature, (*args[:-1], options)))
            response, results = self.conn.recv_until_filtered(queue, timeout=timeout).body
        if response != 0:
            raise RuntimeError("Screen sharing was cancelled" if response == 1 else "Screen sharing was refused")
        return {key: value[1] for key, value in results.items()}

    def start(self, restore_token=None):
        """Returns (PipeWire fd, node id, new restore token)."""
        from jeepney import new_method_call
        session = self._request("CreateSession", "a{sv}",
                                {"session_handle_token": ("s", "buddy" + secrets.token_hex(6))},
                                timeout=30)["session_handle"]
        options = {"types": ("u", MONITOR | WINDOW), "multiple": ("b", False),
                   "cursor_mode": ("u", CURSOR_EMBEDDED), "persist_mode": ("u", PERSIST_UNTIL_REVOKED)}
        if restore_token:
            options["restore_token"] = ("s", restore_token)
        self._request("SelectSources", "oa{sv}", session, options, timeout=30)
        started = self._request("Start", "osa{sv}", session, "", {})
        node_id = started["streams"][0][0]
        reply = self.conn.send_and_get_reply(
            new_method_call(self.addr, "OpenPipeWireRemote", "oa{sv}", (session, {})))
        return reply.body[0].to_raw_fd(), node_id, started.get("restore_token")


def main(out):
    try:
        token = TOKEN_FILE.read_text().strip() or None
    except OSError:
        token = None
    say(state="asking")
    try:
        portal = Portal()
        fd, node_id, new_token = portal.start(token)
    except Exception as exc:                    # no portal, cancelled, timed out
        say(error=str(exc) or type(exc).__name__)
        return 1
    if new_token:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(new_token)
        TOKEN_FILE.chmod(0o600)
    # keep the portal connection (portal.conn) open: closing it ends the screen share
    gst = subprocess.Popen(pipeline(node_id, out, fd), pass_fds=(fd,), stdout=subprocess.DEVNULL,
                           stderr=subprocess.PIPE, text=True)

    def stop(*_):
        if gst.poll() is None:
            gst.send_signal(signal.SIGINT)      # -e: end of stream, so the file is finalized
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)
    say(state="recording", at=time.time())
    # Drain stderr while running: a full pipe otherwise freezes capture.
    _, stderr = gst.communicate()
    code = gst.returncode
    if code != 0:
        err = [line for line in (stderr or "").splitlines() if line.strip()]
        say(error="screen recorder stopped: " + (err[-1] if err else f"exit {code}"))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
