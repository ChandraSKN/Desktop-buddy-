"""The meeting reminder card that stays above Buddy until you Join or Dismiss."""

from datetime import UTC, datetime

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..services.reminders import local_time, meeting_text
from .styles import CARD_CSS
from .timers import after


class MeetingCard(QWidget):
    """A meeting reminder that stays above Buddy until you Join/Dismiss (or it ends)."""

    def __init__(self, buddy):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(CARD_CSS)
        self.buddy = buddy
        self.event = None
        self.setFixedWidth(320)

        card = QWidget(self, objectName="card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(4)
        self.when = QLabel(objectName="when")
        self.title = QLabel(objectName="title", wordWrap=True)
        self.time = QLabel(objectName="time")
        self.link = QLabel(objectName="link", openExternalLinks=True,
                           textInteractionFlags=Qt.TextInteractionFlag.TextBrowserInteraction)
        self.nolink = QLabel("No meeting link in this invite.", objectName="nolink")
        for w in (self.when, self.title, self.time, self.link, self.nolink):
            lay.addWidget(w)
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        self.copy = QPushButton("Copy link", clicked=self.copy_link)
        row.addWidget(self.copy)
        row.addStretch()
        row.addWidget(QPushButton("Dismiss", clicked=self.dismiss))
        self.join = QPushButton("Join meeting", objectName="join", clicked=self.open_link)
        row.addWidget(self.join)
        lay.addLayout(row)

        self._tick = QTimer(self, timeout=self.refresh)

    def show_event(self, event):
        self.event = event
        has_link = bool(event.url)
        self.join.setText("Join meeting" if has_link else "Open in Outlook")
        self.copy.setVisible(has_link)
        self.link.setVisible(has_link)
        self.nolink.setVisible(not has_link)
        if has_link:
            shown = event.url if len(event.url) <= 48 else event.url[:45] + "…"
            self.link.setText(f'<a href="{event.url}" style="color:#8fb4ff">{shown}</a>')
        self.title.setText(event.title)
        self.time.setText(f"{local_time(event.start)} – {local_time(event.end)}")
        self.refresh()
        self.adjustSize()
        self.show()
        self.raise_()
        self.follow(self.buddy)
        self._tick.start(15000)

    def refresh(self):
        if self.event is None:
            return
        now = datetime.now(UTC)
        if now >= self.event.end:
            self.hide_card()
            return
        _, when = meeting_text(self.event, now)
        self.when.setText(f"📅  MEETING {when.upper()}")

    def follow(self, buddy):
        if self.isVisible():
            screen = QGuiApplication.primaryScreen().availableGeometry()
            x = min(screen.right() - self.width() - 8,
                    max(screen.left() + 8, buddy.char_x() - self.width() // 2))
            self.move(x, max(screen.top() + 8, buddy.y() + 4 - self.height()))

    def copy_link(self):
        if self.event and self.event.url:
            QApplication.clipboard().setText(self.event.url)
            self.copy.setText("Copied ✔")
            after(self, 1500, lambda: self.copy.setText("Copy link"))

    def open_link(self):
        if self.event and self.event.url:
            self.buddy.join_meeting(self.event)
        elif self.event:
            QDesktopServices.openUrl(QUrl(self.buddy.outlook_url()))
            self.buddy.acknowledge(self.event.key)

    def dismiss(self):
        if self.event:
            self.buddy.acknowledge(self.event.key)
        else:
            self.hide_card()

    def hide_card(self):
        self._tick.stop()
        self.event = None
        self.hide()
        self.buddy.wave_until = 0
