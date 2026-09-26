"""How long since you last used the keyboard or mouse, from GNOME's idle monitor.

Mutter tracks input for the whole session (Wayland and X11 apps alike), which an X11
window like Buddy can't see on its own. `away` / `returned(seconds_away)` fire when you
cross AWAY_AFTER; if the monitor isn't available (another desktop), nothing fires."""

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

SERVICE = "org.gnome.Mutter.IdleMonitor"
PATH = "/org/gnome/Mutter/IdleMonitor/Core"


class AwayTracker:
    """Pure state machine: feed it idle seconds, it says when you left and came back."""

    def __init__(self, away_after):
        self.away_after = away_after
        self.away = False
        self.away_since_idle = 0.0

    def update(self, idle):
        """Returns "away", ("returned", seconds_away) or None."""
        if not self.away and idle >= self.away_after:
            self.away = True
            return "away"
        if self.away:
            if idle < self.away_after:
                self.away = False
                return ("returned", self.away_since_idle)
            self.away_since_idle = idle
        return None


class IdleMonitor(QObject):
    away = pyqtSignal()
    returned = pyqtSignal(float)          # how long you were idle, in seconds

    def __init__(self, away_after, parent=None, poll_ms=2000):
        super().__init__(parent)
        self.tracker = AwayTracker(away_after)
        self.idle_seconds = 0.0
        bus = QDBusConnection.sessionBus()
        self.iface = QDBusInterface(SERVICE, PATH, SERVICE, bus) if bus.isConnected() else None
        self.available = self.iface is not None and self.iface.isValid()
        if self.available:
            self._timer = QTimer(self, timeout=self.poll)
            self._timer.start(poll_ms)

    def poll(self):
        reply = self.iface.call("GetIdletime")
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or not reply.arguments():
            return
        self.idle_seconds = int(reply.arguments()[0]) / 1000
        event = self.tracker.update(self.idle_seconds)
        if event == "away":
            self.away.emit()
        elif event:
            self.returned.emit(event[1])
