"""Desktop notifications through the freedesktop D-Bus service (GNOME's banners).

Talking to D-Bus directly (instead of running notify-send) gives us the notification id
right away, so a repeated meeting reminder can *replace* the previous one instead of
stacking up, and button clicks arrive as a signal. notify-send is the fallback."""

import html
import shutil

from PyQt6.QtCore import QMetaType, QObject, QProcess, pyqtSignal, pyqtSlot
from PyQt6.QtDBus import QDBusArgument, QDBusConnection, QDBusInterface, QDBusMessage, QDBusVariant

from ..config import ICON

SOUNDS = {"meeting": "message-new-instant", "wellness": "complete"}

SERVICE = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
UINT = QMetaType.Type.UInt.value
BYTE = QMetaType.Type.UChar.value
STRINGS = QMetaType.Type.QStringList.value


class Notifier(QObject):
    """Meeting notifications are 'critical', so GNOME keeps them on screen until they're
    dismissed. Button clicks are reported via `action(key, name)`."""

    action = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ids = {}       # key -> notification id currently on screen
        bus = QDBusConnection.sessionBus()
        self.iface = QDBusInterface(SERVICE, PATH, SERVICE, bus) if bus.isConnected() else None
        if self.iface is not None and self.iface.isValid():
            bus.connect(SERVICE, PATH, SERVICE, "ActionInvoked", self._on_action)
            bus.connect(SERVICE, PATH, SERVICE, "NotificationClosed", self._on_closed)
        else:
            self.iface = None
        self.available = self.iface is not None or shutil.which("notify-send") is not None

    def notify(self, title, body, kind="wellness", key="", actions=()):
        """actions: [(name, label), ...] buttons shown on the notification.
        Calling again with the same key updates that notification in place."""
        if self.iface is None:
            return self._notify_send(title, body, kind)
        flat = [part for pair in actions for part in pair]
        hints = {"urgency": QDBusVariant(QDBusArgument(2 if kind == "meeting" else 1, BYTE)),
                 "sound-name": QDBusVariant(SOUNDS.get(kind, "complete")),
                 "desktop-entry": QDBusVariant("desktop-buddy")}
        reply = self.iface.call("Notify", "Desktop Buddy", QDBusArgument(self._ids.get(key, 0), UINT),
                                ICON, title, html.escape(body, quote=False),
                                QDBusArgument(flat, STRINGS), hints, 0)
        if reply.type() == QDBusMessage.MessageType.ReplyMessage and reply.arguments():
            if key:
                self._ids[key] = int(reply.arguments()[0])
            return True
        return self._notify_send(title, body, kind)

    def close(self, key):
        nid = self._ids.pop(key, None)
        if nid and self.iface is not None:
            self.iface.call("CloseNotification", QDBusArgument(nid, UINT))

    @pyqtSlot(QDBusMessage)
    def _on_action(self, msg):
        nid, name = msg.arguments()
        key = next((k for k, v in self._ids.items() if v == nid), None)
        if key is not None:
            self.action.emit(key, str(name))

    @pyqtSlot(QDBusMessage)
    def _on_closed(self, msg):
        nid = msg.arguments()[0]
        # Closed with the ✕ (not Dismiss): forget the id so the next repeat shows a new banner.
        self._ids = {k: v for k, v in self._ids.items() if v != nid}

    def _notify_send(self, title, body, kind):
        if not shutil.which("notify-send"):
            return False
        QProcess.startDetached("notify-send", [
            "--app-name=Desktop Buddy", f"--icon={ICON}",
            f"--urgency={'critical' if kind == 'meeting' else 'normal'}",
            f"--hint=string:sound-name:{SOUNDS.get(kind, 'complete')}", title, body])
        return True
