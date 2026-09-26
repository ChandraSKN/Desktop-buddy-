"""Start Desktop Buddy."""

import os
import signal
import sys
import time

# GNOME on Wayland doesn't let apps position their own windows; XWayland does.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PyQt6.QtWidgets import QApplication  # noqa: E402  (needs QT_QPA_PLATFORM set first)

from .ui.buddy_window import Buddy  # noqa: E402


def describe(buddy):
    """One line of live state, for `systemctl --user kill -s USR1 desktop-buddy`."""
    return (f"state={buddy.state} chair={buddy.chair and buddy.chair.phase} "
            f"paused={buddy.paused} hovered={buddy.hovered} frozen={buddy.frozen()} "
            f"busy={buddy.busy} card={buddy.card.isVisible()} "
            f"since_click={time.monotonic() - buddy.last_click:.0f}s "
            f"you_idle={buddy.idle.idle_seconds:.0f}s away={buddy.idle.tracker.away} "
            f"minutes={buddy.minutes.state if buddy.minutes.isVisible() else None} "
            f"recording={buddy.minutes.recorder.recording}")


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Desktop Buddy")
    buddy = Buddy()
    buddy.show()
    signal.signal(signal.SIGUSR1, lambda *_: print(describe(buddy), flush=True))
    sys.exit(app.exec())
