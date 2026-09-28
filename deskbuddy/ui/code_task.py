"""Claude Code tasks in the app: the process that runs one, and the card that asks before
it starts and then shows its progress (see services/code_tasks.py for why)."""

import os
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QProcessEnvironment, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..services.code_tasks import CONTINUE_WITHIN, CodeTask, claude_path, command, parse_line
from .styles import CARD_CSS

SHOWN_STEPS = 5


class CodeRunner(QObject):
    """Runs one Claude Code task at a time. `propose`, `plan` and `snapshot` are safe to call
    from the assistant's thread."""

    proposed = pyqtSignal(object)          # CodeTask, for the card to ask about
    progress = pyqtSignal(str)
    finished = pyqtSignal(object, bool, str)   # CodeTask, ok, final message

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._sessions = {}                # folder -> (session id, when it finished)
        self._steps, self._running = [], None
        self.proc = None
        self._buffer, self._result = b"", None

    # ---- called from the assistant's thread
    def plan(self, folder, task, fresh=False):
        with self._lock:
            session, ended = self._sessions.get(Path(folder), (None, 0.0))
        recent = session and time.monotonic() - ended < CONTINUE_WITHIN
        return CodeTask(Path(folder), task.strip(), None if fresh or not recent else session)

    def propose(self, task):
        self.proposed.emit(task)

    def snapshot(self):
        """(task or None, steps so far) of the running task."""
        with self._lock:
            return self._running, list(self._steps)

    @property
    def busy(self):
        return self._running is not None

    # ---- UI thread
    def start(self, task):
        claude = claude_path()
        if claude is None:
            self.finished.emit(task, False, "Claude Code isn't installed (no `claude` command).")
            return False
        with self._lock:
            self._running, self._steps = task, []
        self._buffer, self._result = b"", None
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(str(task.folder))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("BUDDY_CODE_TASK", "1")            # our Claude Code hooks stay quiet: we report it
        env.insert("PATH", os.pathsep.join([str(Path(claude).parent), env.value("PATH", "/usr/bin:/bin")]))
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.ForwardedErrorChannel)
        self.proc.setStandardInputFile(QProcess.nullDevice())
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._ended)
        argv = command(claude, task)
        self.proc.start(argv[0], argv[1:])
        return True

    def stop(self):
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self._result = (False, "Stopped. Anything it already changed stays changed.")
            self.proc.terminate()
            if not self.proc.waitForFinished(3000):
                self.proc.kill()

    def _read(self):
        self._buffer += bytes(self.proc.readAllStandardOutput())
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            for item in parse_line(line.decode(errors="replace")):
                if item[0] == "session":
                    with self._lock:
                        self._sessions[self._running.folder] = (item[1], time.monotonic())
                elif item[0] == "step":
                    with self._lock:
                        self._steps.append(item[1])
                    self.progress.emit(item[1])
                elif self._result is None:
                    self._result = (item[1], item[2])

    def _ended(self, code, _status):
        task = self._running
        with self._lock:
            self._running = None
            if task.folder in self._sessions:            # "continue" counts from the end
                self._sessions[task.folder] = (self._sessions[task.folder][0], time.monotonic())
        ok, text = self._result or (False, f"Claude Code stopped (exit code {code}) without an answer.")
        self.finished.emit(task, ok, text)


class CodeCard(QWidget):
    """"Run this with Claude Code in desktop-buddy?" with Run / Cancel; then its progress."""

    def __init__(self, buddy):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(CARD_CSS + "QLabel#brief { font-size: 13px; } "
                           "QLabel#steps { color: #b9c3e6; font-size: 11px; }")
        self.buddy = buddy
        self.proposal = None             # a CodeTask waiting for yes/no
        self.steps = []
        self.setFixedWidth(340)

        card = QWidget(self, objectName="card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(4)
        self.head = QLabel(objectName="when")
        self.folder = QLabel(objectName="title", wordWrap=True)
        self.text = QLabel(objectName="brief", wordWrap=True, textFormat=Qt.TextFormat.PlainText)
        self.log = QLabel(objectName="steps", wordWrap=True, textFormat=Qt.TextFormat.PlainText)
        for w in (self.head, self.folder, self.text, self.log):
            lay.addWidget(w)
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        self.hint = QLabel(objectName="time")
        row.addWidget(self.hint)
        row.addStretch()
        self.cancel_btn = QPushButton("Cancel", clicked=self._cancel)
        self.run_btn = QPushButton("Run", objectName="join", clicked=lambda: self.buddy.answer_code(True))
        row.addWidget(self.cancel_btn)
        row.addWidget(self.run_btn)
        lay.addLayout(row)

    def ask(self, task):
        self.proposal = task
        self.head.setText("🧑‍💻  RUN WITH CLAUDE CODE?" + ("  (continuing)" if task.resume else ""))
        self.folder.setText(f"📁 {task.folder.name}")
        self.folder.setToolTip(str(task.folder))
        self.text.setText(task.task)
        self.log.hide()
        self.hint.setText("or say “yes” / “no” · it can edit files and run commands")
        self.cancel_btn.setText("Cancel")
        self.run_btn.show()
        self._show()

    def running(self, task):
        self.proposal = None
        self.steps = []
        self.head.setText("🧑‍💻  CLAUDE CODE IS WORKING…")
        self.log.setText("⏳ Starting…")
        self.log.show()
        self.hint.setText("VS Code shows the changes")
        self.cancel_btn.setText("Stop")
        self.run_btn.hide()
        self._show()

    def add_step(self, text):
        self.steps.append(text)
        self.log.setText("\n".join(self.steps[-SHOWN_STEPS:]))
        self.adjustSize()
        self.follow(self.buddy)

    def done(self, ok, summary):
        self.head.setText("✅  CLAUDE CODE IS DONE" if ok else "⚠️  CLAUDE CODE STOPPED")
        self.log.setText(summary)
        self.hint.setText(f"{len(self.steps)} steps")
        self.cancel_btn.setText("Close")
        self._show()

    def hide_card(self):
        self.proposal = None
        self.hide()

    def _cancel(self):
        if self.proposal is not None:
            self.buddy.answer_code(False)
        elif self.buddy.code.busy:
            self.buddy.code.stop()
        else:
            self.hide_card()

    def _show(self):
        self.adjustSize()
        self.show()
        self.raise_()
        self.follow(self.buddy)

    def follow(self, buddy):
        if self.isVisible():
            screen = QGuiApplication.primaryScreen().availableGeometry()
            x = min(screen.right() - self.width() - 8,
                    max(screen.left() + 8, buddy.char_x() - self.width() // 2))
            tops = [w.y() for w in (buddy.card, buddy.minutes, buddy.draft_card) if w.isVisible()]
            below = min(tops) if tops else buddy.y() + 4
            self.move(x, max(screen.top() + 8, below - self.height() - 4))
