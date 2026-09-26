"""WhatsApp in the app: the linked-device helper process, the QR to link it, and the card
that asks before a message goes out (see services/whatsapp.py for why)."""

import itertools
import json
import sys
import threading
from io import BytesIO
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QPixmap
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..config import ROOT, WHATSAPP_DB
from ..services.whatsapp import Contact, Draft, clean_message
from .styles import CARD_CSS, PANEL_CSS

CONTACTS_EVERY = 10 * 60 * 1000     # the phone keeps syncing the address book after linking


class WhatsAppLink(QObject):
    """Runs services/whatsapp_worker.py and keeps its contacts. Safe to read `contacts` and
    call `propose` from the assistant's thread."""

    state_changed = pyqtSignal(str)      # "not linked", "linking", "connecting", "connected", …
    qr = pyqtSignal(str)
    draft_ready = pyqtSignal(object)     # Draft, for the UI thread to show
    sent = pyqtSignal(object, bool, str)  # Draft, ok, error

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "not linked"
        self._contacts, self._lock = [], threading.Lock()
        self._ids, self._sending = itertools.count(1), {}
        self.proc = None
        self._refresh = QTimer(self, timeout=lambda: self._command(cmd="contacts"))

    @property
    def linked(self):
        """Has this computer been linked before (so it should connect at start-up)?"""
        return Path(WHATSAPP_DB).exists()

    @property
    def contacts(self):
        with self._lock:
            return list(self._contacts)

    def start(self):
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            return
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.ForwardedErrorChannel)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._ended)
        self.proc.setWorkingDirectory(str(ROOT))
        self.proc.start(sys.executable, ["-m", "deskbuddy.services.whatsapp_worker", WHATSAPP_DB])
        self._set_state("connecting" if self.linked else "linking")

    def stop(self):
        self._refresh.stop()
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.closeWriteChannel()             # the worker exits when stdin closes
            if not self.proc.waitForFinished(2000):
                self.proc.kill()
                self.proc.waitForFinished(1000)

    def unlink(self):
        """Log this device out of WhatsApp (it disappears from Linked devices on the phone)."""
        if self.state == "connected":
            self._command(cmd="logout")
        else:
            self.stop()
            self._forget_session()

    # called from the assistant's thread
    def propose(self, contact, text):
        self.draft_ready.emit(Draft(contact, clean_message(text)))

    def send(self, draft):
        if self.state != "connected":
            self.sent.emit(draft, False, "WhatsApp isn't connected")
            return
        n = next(self._ids)
        self._sending[n] = draft
        self._command(cmd="send", id=n, jid=draft.contact.jid, text=draft.text)

    # ---- the helper process
    def _command(self, **msg):
        if self.proc and self.proc.state() == QProcess.ProcessState.Running:
            self.proc.write((json.dumps(msg, ensure_ascii=False) + "\n").encode())

    def _read(self):
        while self.proc.canReadLine():
            try:
                event = json.loads(bytes(self.proc.readLine()).decode())
            except ValueError:
                continue
            kind = event.get("event")
            if kind == "qr":
                self._set_state("linking")
                self.qr.emit(event["data"])
            elif kind == "connected":
                self._set_state("connected")
                self._command(cmd="contacts")
                QTimer.singleShot(30000, lambda: self._command(cmd="contacts"))
                self._refresh.start(CONTACTS_EVERY)
            elif kind == "contacts":
                with self._lock:
                    self._contacts = [Contact(c["jid"], c["name"]) for c in event["items"]]
            elif kind == "sent":
                draft = self._sending.pop(event.get("id"), None)
                if draft:
                    self.sent.emit(draft, bool(event.get("ok")), event.get("error", ""))
            elif kind == "logged_out":
                self.stop()
                self._forget_session()
            elif kind == "error":
                print(f"whatsapp: {event.get('message')}", flush=True)

    def _ended(self, *_):
        self._refresh.stop()
        for draft in list(self._sending.values()):
            self.sent.emit(draft, False, "WhatsApp stopped")
        self._sending.clear()
        if self.state != "not linked":
            self._set_state("stopped")

    def _forget_session(self):
        for suffix in ("", "-wal", "-shm", "-journal"):
            Path(WHATSAPP_DB + suffix).unlink(missing_ok=True)
        with self._lock:
            self._contacts = []
        self._set_state("not linked")

    def _set_state(self, state):
        if state != self.state:
            self.state = state
            self.state_changed.emit(state)


def qr_pixmap(data, size=300):
    """The code at a whole number of pixels per module, as large as fits in `size`."""
    import segno
    qr = segno.make_qr(data)
    scale = max(2, size // qr.symbol_size(border=3)[0])
    buf = BytesIO()
    qr.save(buf, kind="png", scale=scale, border=3, dark="#11141f", light="#ffffff")
    pix = QPixmap()
    pix.loadFromData(buf.getvalue(), "PNG")
    return pix


class LinkDialog(QDialog):
    """Scan to link: WhatsApp on the phone → Settings → Linked devices → Link a device."""

    def __init__(self, link, parent=None):
        super().__init__(parent, Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Link WhatsApp")
        self.setStyleSheet("QDialog { background: #1d2233; }" + PANEL_CSS)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        title = QLabel("Link Buddy to your WhatsApp", objectName="title")
        steps = QLabel("On your phone open WhatsApp → Settings → <b>Linked devices</b> → "
                       "<b>Link a device</b>, and scan this code.", wordWrap=True)
        self.code = QLabel("Getting a code…", alignment=Qt.AlignmentFlag.AlignCenter)
        self.code.setMinimumSize(300, 300)
        self.status = QLabel("Buddy only sends messages you confirm. It doesn't read your chats.",
                             objectName="status", wordWrap=True)
        for w in (title, steps, self.code, self.status):
            lay.addWidget(w)
        lay.addWidget(QPushButton("Close", clicked=self.close), alignment=Qt.AlignmentFlag.AlignRight)
        self.setFixedWidth(360)
        link.qr.connect(self.show_code)
        link.state_changed.connect(self.on_state)

    def show_code(self, data):
        self.code.setPixmap(qr_pixmap(data))

    def on_state(self, state):
        if state == "connected":
            self.code.setText("✔ Linked!")
            QTimer.singleShot(1500, self.close)


class DraftCard(QWidget):
    """"Send this on WhatsApp to Ravi?" with Send / Cancel, above Buddy."""

    def __init__(self, buddy):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(CARD_CSS + "QLabel#brief { font-size: 13px; }")
        self.buddy = buddy
        self.draft = None
        self.setFixedWidth(320)

        card = QWidget(self, objectName="card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(4)
        lay.addWidget(QLabel("💬  SEND ON WHATSAPP?", objectName="when"))
        self.to = QLabel(objectName="title", wordWrap=True)
        self.phone = QLabel(objectName="time")
        self.text = QLabel(objectName="brief", wordWrap=True,
                           textFormat=Qt.TextFormat.PlainText)
        for w in (self.to, self.phone, self.text):
            lay.addWidget(w)
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        self.hint = QLabel("or say “yes” / “no”", objectName="time")
        row.addWidget(self.hint)
        row.addStretch()
        row.addWidget(QPushButton("Cancel", clicked=lambda: self.buddy.answer_draft(False)))
        self.send_btn = QPushButton("Send", objectName="join",
                                    clicked=lambda: self.buddy.answer_draft(True))
        row.addWidget(self.send_btn)
        lay.addLayout(row)

    def show_draft(self, draft):
        self.draft = draft
        self.to.setText(f"To {draft.contact.name}")
        self.phone.setText(draft.contact.phone)
        self.text.setText(draft.text)
        self.send_btn.setEnabled(True)
        self.adjustSize()
        self.show()
        self.raise_()
        self.follow(self.buddy)

    def sending(self):
        self.send_btn.setEnabled(False)
        self.send_btn.setText("Sending…")

    def hide_card(self):
        self.draft = None
        self.send_btn.setText("Send")
        self.hide()

    def follow(self, buddy):
        if self.isVisible():
            screen = QGuiApplication.primaryScreen().availableGeometry()
            x = min(screen.right() - self.width() - 8,
                    max(screen.left() + 8, buddy.char_x() - self.width() // 2))
            tops = [w.y() for w in (buddy.card, buddy.minutes) if w.isVisible()]
            below = min(tops) if tops else buddy.y() + 4
            self.move(x, max(screen.top() + 8, below - self.height() - 4))
