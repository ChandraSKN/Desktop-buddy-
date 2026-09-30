"""The "Fix my text" panel: paste text, get it corrected with an explanation of each fix."""

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QGuiApplication
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..services import corrector
from .styles import PANEL_CSS


class Worker(QThread):
    done = pyqtSignal(str, list)
    failed = pyqtSignal(str)

    def __init__(self, text, tone="Original"):
        super().__init__()
        self.text = text
        self.tone = tone

    def run(self):
        try:
            self.done.emit(*corrector.correct(self.text, self.tone))
        except Exception as e:
            self.failed.emit(friendly_error(e))


def friendly_error(e):
    try:
        import anthropic
        if isinstance(e, anthropic.AuthenticationError):
            return "Your Claude API key was rejected. Check ~/.config/desktop-buddy/api_key."
        if isinstance(e, anthropic.RateLimitError):
            return "Rate limited — wait a moment and try again."
        if isinstance(e, anthropic.APIConnectionError):
            return "Couldn't reach Claude. Are you online?"
        if isinstance(e, anthropic.APIStatusError):
            return f"Claude API error {e.status_code}: {e.message}"
    except ImportError:
        pass
    return f"Something went wrong: {e}"


class CorrectorPanel(QWidget):
    def __init__(self, buddy):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.buddy = buddy
        self.worker = None
        self._drag = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(PANEL_CSS)
        self.resize(480, 620)

        card = QWidget(self, objectName="card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(QLabel("✍️  Fix my text", objectName="title"))
        head.addStretch()
        close = QPushButton("✕", objectName="close", clicked=self.close)
        head.addWidget(close)
        lay.addLayout(head)

        tone_row = QHBoxLayout()
        tone_label = QLabel("Tone")
        tone_row.addWidget(tone_label)
        self.tone = QComboBox()
        self.tone.addItems(corrector.TONES)
        self.tone.setAccessibleName("Writing tone")
        self.tone.setToolTip("Original keeps your tone; other options rewrite the style as well as fixing grammar.")
        tone_label.setBuddy(self.tone)
        tone_row.addWidget(self.tone, 1)
        lay.addLayout(tone_row)

        self.input = QPlainTextEdit(placeholderText="Paste the text you want corrected…   (Ctrl+Enter to fix)")
        lay.addWidget(self.input, 3)

        row = QHBoxLayout()
        row.addWidget(QPushButton("📋 Paste", clicked=self.paste))
        row.addWidget(QPushButton("Clear", clicked=self.clear))
        row.addStretch()
        self.fix_btn = QPushButton("Fix it ✨", objectName="primary", clicked=self.run_fix)
        row.addWidget(self.fix_btn)
        lay.addLayout(row)

        self.output = QPlainTextEdit(readOnly=True, placeholderText="Corrected text will appear here.")
        lay.addWidget(self.output, 3)

        self.changes_title = QLabel("What I fixed", objectName="status")
        lay.addWidget(self.changes_title)
        self.changes = QPlainTextEdit(readOnly=True, objectName="changes",
                                      placeholderText="An explanation of each correction will appear here.")
        lay.addWidget(self.changes, 2)

        row2 = QHBoxLayout()
        self.status = QLabel("", objectName="status")
        row2.addWidget(self.status, 1)
        self.copy_btn = QPushButton("Copy", clicked=self.copy)
        row2.addWidget(self.copy_btn)
        lay.addLayout(row2)

        self.addAction(QAction(self, shortcut="Ctrl+Return", triggered=self.run_fix))
        self.addAction(QAction(self, shortcut="Escape", triggered=self.close))

    def show_near(self, buddy):
        scr = QGuiApplication.primaryScreen().availableGeometry()
        x = buddy.x() + buddy.width() if buddy.x() + buddy.width() + self.width() < scr.right() \
            else buddy.x() - self.width()
        y = scr.bottom() - self.height() - 40
        self.move(max(scr.left(), x), max(scr.top(), y))
        self.status.setText(f"Using {corrector.backend_name()}")
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def clear(self):
        for box in (self.input, self.output, self.changes):
            box.clear()

    def paste(self):
        self.input.setPlainText(QApplication.clipboard().text())
        self.input.setFocus()

    def run_fix(self):
        text = self.input.toPlainText().strip()
        if not text or (self.worker and self.worker.isRunning()):
            return
        self.fix_btn.setEnabled(False)
        self.tone.setEnabled(False)
        self.fix_btn.setText("Thinking…")
        self.status.setText(f"Correcting with {corrector.backend_name()}…")
        self.output.clear()
        self.changes.clear()
        self.worker = Worker(text, self.tone.currentText())
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def on_done(self, fixed, changes):
        self.output.setPlainText(fixed)
        self.changes.setPlainText("\n".join(f"• {c}" for c in changes))
        self.fix_btn.setEnabled(True)
        self.tone.setEnabled(True)
        self.fix_btn.setText("Fix it ✨")
        same = fixed.strip() == self.input.toPlainText().strip()
        if same and not changes:
            self.status.setText("Looks good already, nothing to fix 👍")
        else:
            n = len(changes)
            self.status.setText(f"{n} fix{'es' if n != 1 else ''} made. Click Copy to use it.")

    def on_failed(self, msg):
        self.fix_btn.setEnabled(True)
        self.tone.setEnabled(True)
        self.fix_btn.setText("Fix it ✨")
        self.status.setText(msg)

    def copy(self):
        if self.output.toPlainText():
            QApplication.clipboard().setText(self.output.toPlainText())
            self.status.setText("Copied to clipboard ✔")

    # drag the panel by its header
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
        self.buddy.say("Happy to help!", 2000)
