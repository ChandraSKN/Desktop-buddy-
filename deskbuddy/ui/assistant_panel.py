"""The "Ask Buddy" chat panel. The agent runs on a worker thread; its text streams in."""

import html

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QDesktopServices, QGuiApplication, QTextCursor
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout, QWidget

from ..services import agent as agent_mod
from .styles import CHAT_CSS

# who -> (background, label, pushed to the right)
BUBBLES = {"you": ("#34427a", "You", True),
           "buddy": ("#262c40", "Buddy", False),
           "error": ("#4a2530", "Buddy", False)}


class AgentWorker(QThread):
    text = pyqtSignal(str)
    action = pyqtSignal(str)
    done = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, agent, message):
        super().__init__()
        self.agent, self.message = agent, message

    def run(self):
        try:
            self.done.emit(self.agent.send(self.message, self.text.emit, self.action.emit))
        except Exception as exc:            # shown in the panel; the app keeps running
            self.failed.emit(agent_mod.friendly_error(exc))


class AssistantPanel(QWidget):
    def __init__(self, buddy, make_agent):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.buddy = buddy
        self.make_agent = make_agent          # () -> Agent, so "New chat" starts fresh
        self.agent = None
        self.worker = None
        self._drag = None
        self._reply = ""
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(CHAT_CSS)
        self.resize(420, 560)

        card = QWidget(self, objectName="card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(QLabel("💬  Ask Buddy", objectName="title"))
        head.addStretch()
        head.addWidget(QPushButton("New chat", objectName="small", clicked=self.new_chat))
        head.addWidget(QPushButton("✍️ Fix text", objectName="small", clicked=self.open_fixer))
        head.addWidget(QPushButton("✕", objectName="close", clicked=self.close))
        lay.addLayout(head)

        self.log = QTextBrowser(openExternalLinks=True)
        lay.addWidget(self.log, 1)

        self.input = QPlainTextEdit(placeholderText="Ask me anything…  e.g. “What's my next meeting?”  "
                                                    "(Enter to send, Shift+Enter for a new line)")
        self.input.setFixedHeight(76)
        self.input.installEventFilter(self)
        lay.addWidget(self.input)

        row = QHBoxLayout()
        self.status = QLabel("", objectName="status")
        row.addWidget(self.status, 1)
        self.send_btn = QPushButton("Send", objectName="primary", clicked=self.send)
        row.addWidget(self.send_btn)
        lay.addLayout(row)

        self.addAction(QAction(self, shortcut="Escape", triggered=self.close))
        self.new_chat()

    # ---- conversation
    def new_chat(self):
        if self.worker and self.worker.isRunning():
            return
        self.agent = None
        self._html = []
        self._render()
        if agent_mod.available():
            self._add("buddy", "Hi! Ask me about your meetings, set a reminder, or tell me "
                               "something to remember.")
            self.status.setText("Claude · remembers what you tell him")
        else:
            self._add("buddy", agent_mod.setup_hint())
            self.status.setText("Not connected")

    def send(self):
        text = self.input.toPlainText().strip()
        if not text or (self.worker and self.worker.isRunning()):
            return
        if not agent_mod.available():
            self._add("buddy", agent_mod.setup_hint())
            return
        if self.agent is None:
            self.agent = self.make_agent()
        self.input.clear()
        self._add("you", text)
        self._reply = ""
        self._add("buddy", "…")
        self.send_btn.setEnabled(False)
        self.status.setText("Thinking…")
        self.worker = AgentWorker(self.agent, text)
        self.worker.text.connect(self.on_text)
        self.worker.action.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def on_text(self, chunk):
        self._reply += chunk
        self._html[-1] = self._bubble("buddy", self._reply.strip() or "…")
        self._render()

    def on_done(self, reply):
        self._html[-1] = self._bubble("buddy", reply or "(no reply)")
        self._render()
        self._finish()
        if not self.isVisible():          # you closed the panel while he was answering
            self.buddy.say(reply[:140] + ("…" if len(reply) > 140 else ""), 8000)

    def on_failed(self, message):
        self._html[-1] = self._bubble("error", message)
        self._render()
        self._finish()

    def _finish(self):
        self.send_btn.setEnabled(True)
        self.status.setText("Claude · remembers what you tell him")
        self.input.setFocus()

    # ---- rendering
    @staticmethod
    def _bubble(who, text):
        """Qt rich text ignores most CSS, but table cells take colours and padding reliably."""
        body = html.escape(text).replace("\n", "<br>")
        colour, label, gap_left = BUBBLES[who]
        cell = (f'<td bgcolor="{colour}" style="padding:8px 10px">'
                f'<span style="color:#9aa6c8;font-size:10px">{label}</span><br>{body}</td>')
        gap = '<td width="18%"></td>'
        row = gap + cell if gap_left else cell + gap
        return f'<table width="100%" cellspacing="4" cellpadding="0"><tr>{row}</tr></table>'

    def _add(self, who, text):
        self._html.append(self._bubble(who, text))
        self._render()

    def _render(self):
        self.log.setHtml("".join(self._html))
        self.log.moveCursor(QTextCursor.MoveOperation.End)

    # ---- window
    def show_near(self, buddy):
        scr = QGuiApplication.primaryScreen().availableGeometry()
        right = buddy.x() + buddy.width()
        x = right if right + self.width() < scr.right() else buddy.x() - self.width()
        self.move(max(scr.left(), x), max(scr.top(), scr.bottom() - self.height() - 40))
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def open_fixer(self):
        self.close()
        self.buddy.open_fixer()

    def eventFilter(self, obj, event):
        if (obj is self.input and event.type() == event.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.send()
            return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 48:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        self._drag = None

    def closeEvent(self, e):
        super().closeEvent(e)
        self.buddy.panel_closed()
