"""Nonblocking, bounded polling of browser media state."""
import json
import os
import sys
from pathlib import Path

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal


class MusicMonitor(QObject):
    changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.playing = False
        self.proc = QProcess(self)
        self.proc.setWorkingDirectory(str(Path(__file__).resolve().parents[2]))
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(lambda _: self._set(False))
        self.timeout = QTimer(self, singleShot=True, timeout=self.proc.kill)
        self.timer = QTimer(self, timeout=self.poll)
        if os.environ.get("BUDDY_MUSIC", "1") != "0":
            self.timer.start(2500)

    def poll(self):
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            return
        self.proc.start(sys.executable, ["-m", "deskbuddy.services.music"])
        self.timeout.start(2000)

    def _finished(self, code, _status):
        self.timeout.stop()
        try:
            data = json.loads(bytes(self.proc.readAllStandardOutput()))
            active = code == 0 and data.get("playing") is True
        except (ValueError, AttributeError):
            active = False
        self._set(active)

    def _set(self, active):
        if active != self.playing:
            self.playing = active
            self.changed.emit(active)

    def stop(self):
        self.timer.stop()
        self.timeout.stop()
        self.proc.kill()
        self.proc.waitForFinished(1000)
