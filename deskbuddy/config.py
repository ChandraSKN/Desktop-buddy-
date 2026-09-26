"""Tunables in one place. Timings can be overridden with environment variables so a demo
doesn't have to wait ten minutes, e.g. `BUDDY_SIT_AFTER=30 ./run.sh`."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICON = str(ASSETS / "icon.png")


def _seconds(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


# window and movement
WIN_W, WIN_H = 200, 300
FPS = 30
WALK_SPEED = 75          # px per second when roaming
DOCK_SPEED = 24          # px per second for the small steps while docked
STEP_RATE = 7.0          # radians of walk-cycle per second

# the chair
SIT_AFTER = _seconds("BUDDY_SIT_AFTER", 1 * 60)   # no clicks for this long: he sits down
CHAIR_PAD = 90           # extra window width on his right (screen-left) while the chair is out

# XWayland may never report the pointer leaving, so a hover lapses this long after the
# pointer last moved over him.
HOVER_HOLD = 3.0

# you, away from the computer (keyboard and mouse idle, from GNOME)
AWAY_AFTER = _seconds("BUDDY_AWAY_AFTER", 5 * 60)

# the assistant's long-term memory (facts + reminders it set)
MEMORY_DB = os.environ.get("BUDDY_MEMORY_DB",
                           str(Path.home() / ".local" / "share" / "desktop-buddy" / "memory.db"))

# WhatsApp, linked as a companion device (the session lives here; delete it to unlink)
WHATSAPP_DB = os.environ.get("BUDDY_WHATSAPP_DB",
                             str(Path.home() / ".local" / "share" / "desktop-buddy" / "whatsapp.db"))

# minutes of meeting
RECORDINGS_DIR = os.environ.get("BUDDY_RECORDINGS_DIR",
                                str(Path.home() / ".local" / "share" / "desktop-buddy" / "recordings"))
MINUTES_DIR = os.environ.get("BUDDY_MINUTES_DIR", str(Path.home() / "Documents" / "Meeting Minutes"))
STOP_AFTER_END = 10 * 60        # still recording this long after the meeting's end: stop
MAX_RECORDING = 4 * 60 * 60     # hard limit, in case the end is unknown
MIC_CHECK_EVERY = 20            # seconds between "is another app using the mic?" checks

# voice: set BUDDY_VOICE=0 to never open the microphone for listening (tests do)
VOICE = os.environ.get("BUDDY_VOICE", "1") != "0"
