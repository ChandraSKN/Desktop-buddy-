"""Minutes of meeting: the bar above Buddy, and the record → transcribe → write pipeline.

  offer      "📝 Take minutes for “Standup”?"         [Start] [Not now]
  recording  "● Recording minutes · 12:34"             [Stop]
  working    "📝 Writing minutes… transcribing 40%"
  done       "✅ Minutes ready: Standup"               [Open] [✕]

Recording only ever starts on a click. It's offered when you join a meeting through Buddy,
or when another app starts using the mic while a calendar meeting is on (you joined some
other way). It stops on Stop, 10 minutes after the meeting's scheduled end, or at 4 hours.
A folder that has audio but no minutes (Buddy quit while working) is picked up at startup."""

import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..config import MAX_RECORDING, MIC_CHECK_EVERY, MINUTES_DIR, RECORDINGS_DIR, ROOT, STOP_AFTER_END
from ..services import minutes as minutes_mod
from ..services.recorder import Recorder, other_apps_using_mic
from .styles import CARD_CSS

MAX_ATTEMPTS = 2


class MinutesWorker(QThread):
    """Transcribe (separate low-priority process) and have Claude write the minutes."""

    progress = pyqtSignal(str)
    done = pyqtSignal(str, str)          # path, title
    failed = pyqtSignal(str, bool)       # message, keep the audio for a retry

    def __init__(self, folder, memory):
        super().__init__()
        self.folder, self.memory = Path(folder), memory
        self.proc = None

    def run(self):
        meta_path = self.folder / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["attempts"] = meta.get("attempts", 0) + 1
        meta_path.write_text(json.dumps(meta, indent=1))
        try:
            segments = self._transcript()
            if segments is None:
                return
            if minutes_mod.word_count(segments) < minutes_mod.MIN_WORDS:
                self._discard_audio()
                self.failed.emit("Hardly anything was said, so there are no minutes to write.", False)
                return
            self.progress.emit("summarising with Claude")
            prefs = [m.text for m in self.memory.recall("meeting minutes summary notes action items")]
            result = minutes_mod.write_minutes(segments, meta["title"], prefs)
            markdown = minutes_mod.render(result, meta, segments)
            path = minutes_mod.save(markdown, self.folder, meta, MINUTES_DIR)
            body = markdown.split("<details>")[0]
            self.memory.add_minutes(meta["title"], datetime.fromisoformat(meta["recorded_from"]), path, body)
            self._discard_audio()
            self.done.emit(str(path), meta["title"])
        except Exception as exc:            # shown on the bar; the audio is kept for a retry
            from ..services.agent import friendly_error
            self.failed.emit(friendly_error(exc), True)

    def _transcript(self):
        cached = self.folder / "transcript.json"
        if cached.exists():
            return json.loads(cached.read_text())
        self.progress.emit("transcribing 0%")
        self.proc = subprocess.Popen(
            ["nice", "-n", "15", sys.executable, "-m", "deskbuddy.services.transcribe", str(self.folder)],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in self.proc.stdout:
            try:
                self.progress.emit(f"transcribing {json.loads(line)['progress']:.0%}")
            except (ValueError, KeyError):
                pass
        if self.proc.wait() != 0:
            err = self.proc.stderr.read().strip().splitlines()
            if self.proc.returncode < 0:            # stopped because Buddy is quitting
                return None
            raise RuntimeError("Transcription failed: " + (err[-1] if err else "unknown error"))
        return json.loads(cached.read_text())

    def _discard_audio(self):
        for name in ("mic.wav", "others.wav"):
            (self.folder / name).unlink(missing_ok=True)

    def cancel(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


class MinutesBar(QWidget):
    def __init__(self, buddy, memory):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(CARD_CSS)
        self.setFixedWidth(320)
        self.buddy, self.memory = buddy, memory
        self.recorder = Recorder(RECORDINGS_DIR)
        self.event = None                 # the meeting being offered / recorded
        self.offered = set()              # meeting keys already offered (ask once)
        self.queue, self.worker = [], None
        self.result_path = None
        self._next_mic_check = 0.0

        card = QWidget(self, objectName="card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(6)
        self.label = QLabel(objectName="title", wordWrap=True)
        self.note = QLabel(objectName="time", wordWrap=True)
        lay.addWidget(self.label)
        lay.addWidget(self.note)
        row = QHBoxLayout()
        row.setContentsMargins(0, 4, 0, 0)
        row.addStretch()
        self.secondary = QPushButton(clicked=self._secondary)
        self.primary = QPushButton(objectName="join", clicked=self._primary)
        row.addWidget(self.secondary)
        row.addWidget(self.primary)
        lay.addLayout(row)
        self.state = None

    # ---- states
    def _show(self, state, label, note="", primary=None, secondary=None):
        self.state = state
        self.label.setText(label)
        self.note.setText(note)
        self.note.setVisible(bool(note))
        for button, text in ((self.primary, primary), (self.secondary, secondary)):
            button.setText(text or "")
            button.setVisible(bool(text))
        self.adjustSize()
        self.show()
        self.follow(self.buddy)

    def _primary(self):
        {"offer": self.start, "recording": self.stop, "done": self.open_result,
         "error": self.hide}.get(self.state, lambda: None)()

    def _secondary(self):
        if self.state in ("offer", "done"):
            self.hide()

    def offer(self, event):
        """Ask to take minutes for event (once per meeting)."""
        if self.recorder.recording or event.key in self.offered:
            return
        self.offered.add(event.key)
        self.event = event
        self._show("offer", f"📝 Take minutes for “{event.title}”?",
                   "Buddy records this call, transcribes it on this computer and writes the "
                   "minutes. Let the others know you're recording.", "Start", "Not now")
        self.buddy.wave(3)

    def start(self, event=None, title=None):
        event = event or self.event
        title = title or (event.title if event else "Meeting")
        try:
            self.recorder.start(title, event)
        except (OSError, RuntimeError) as exc:
            self._show("error", "Couldn't start recording", str(exc), "OK")
            return
        self.event = event
        self.tick()

    def stop(self, reason=""):
        if not self.recorder.recording:
            return
        folder = self.recorder.stop()
        if reason:
            self.buddy.say(reason, 6000)
        self.queue.append(folder)
        self._next_job()

    # ---- called every second by Buddy
    def tick(self):
        now = datetime.now(UTC)
        if self.recorder.recording:
            error = self.recorder.failed()
            if error:
                self.recorder.stop()
                self._show("error", "Recording stopped", error[:200], "OK")
                return
            secs = int((now - self.recorder.started).total_seconds())
            dot = '<span style="color:#ff5a6a">●</span>'
            self._show("recording", f"{dot} Recording minutes · {secs // 60:02d}:{secs % 60:02d}",
                       self.event.title if self.event else "", "Stop")
            if self.event and now > self.event.end + timedelta(seconds=STOP_AFTER_END):
                self.stop("The meeting's over, so I stopped recording. Writing the minutes now.")
            elif secs > MAX_RECORDING:
                self.stop("That's 4 hours, so I stopped recording. Writing the minutes now.")
        elif self.worker is None and time.monotonic() >= self._next_mic_check:
            self._next_mic_check = time.monotonic() + MIC_CHECK_EVERY
            self._check_mic(now)

    def _check_mic(self, now):
        """A meeting is on and some other app is using the mic: you've probably joined it."""
        live = [e for e in self.buddy.events
                if e.start - timedelta(minutes=5) <= now <= e.end and e.key not in self.offered]
        if live and other_apps_using_mic():
            self.offer(live[0])

    # ---- processing
    def resume_pending(self):
        """Recordings that never got minutes (quit mid-way, or a failure): process them now."""
        root = Path(RECORDINGS_DIR)
        for folder in sorted(root.glob("*/")) if root.exists() else []:
            meta_path = folder / "meta.json"
            if not meta_path.exists() or (folder / "minutes.json").exists():
                continue
            meta = json.loads(meta_path.read_text())
            if meta.get("attempts", 0) >= MAX_ATTEMPTS:
                continue
            has_audio = any((folder / n).exists() for n in ("mic.wav", "others.wav"))
            if not has_audio and not (folder / "transcript.json").exists():
                continue
            if "recorded_to" not in meta:       # Buddy stopped while recording
                newest = max((folder / n).stat().st_mtime for n in ("mic.wav", "others.wav")
                             if (folder / n).exists())
                meta["recorded_to"] = datetime.fromtimestamp(newest, UTC).isoformat()
                meta_path.write_text(json.dumps(meta, indent=1))
            self.queue.append(folder)
        self._next_job()

    def _next_job(self):
        if self.worker is not None or not self.queue:
            return
        folder = self.queue.pop(0)
        self.worker = MinutesWorker(folder, self.memory)
        title = json.loads((folder / "meta.json").read_text())["title"]
        self.worker.progress.connect(lambda text, t=title: self._show(
            "working", f"📝 Writing minutes… {text}", t))
        self.worker.done.connect(self._done)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._worker_finished)
        self._show("working", "📝 Writing minutes…", title)
        self.worker.start()

    def _worker_finished(self):
        self.worker = None
        self._next_job()

    def _done(self, path, title):
        self.result_path = path
        self._show("done", f"✅ Minutes ready: {title}", "Saved to " + str(Path(path).parent),
                   "Open", "✕")
        self.buddy.notifier.notify("📝 Minutes ready", f"{title}\n{path}", "wellness", "minutes",
                                   [("open-minutes", "Open")])
        self.buddy.say("Your minutes are ready! 📝", 5000)
        self.buddy.wave(4)

    def _failed(self, message, kept_audio):
        note = message + (" The recording is kept; I'll try again next time I start." if kept_audio else "")
        self._show("error", "Couldn't write the minutes", note, "OK")

    def open_result(self):
        if self.result_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.result_path))
        self.hide()

    def shutdown(self):
        """Buddy is quitting: finish the audio files; minutes are resumed at the next start."""
        if self.recorder.recording:
            self.recorder.stop()
        if self.worker:
            self.worker.cancel()
            self.worker.wait(3000)

    # ---- placement
    def follow(self, buddy):
        if self.isVisible():
            screen = QGuiApplication.primaryScreen().availableGeometry()
            x = min(screen.right() - self.width() - 8,
                    max(screen.left() + 8, buddy.char_x() - self.width() // 2))
            below = buddy.card.y() if buddy.card.isVisible() else buddy.y() + 4
            self.move(x, max(screen.top() + 8, below - self.height() - 4))
