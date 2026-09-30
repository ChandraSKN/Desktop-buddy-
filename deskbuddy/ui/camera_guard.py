"""Session-only face enrollment, local camera recognition and cancellable shutdown."""

import math
import time

from PyQt6.QtCore import QObject, QProcess, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout

from ..services.camera_guard import GuardPolicy


class CameraThread(QThread):
    presence = pyqtSignal(str)
    status = pyqtSignal(str)
    failed = pyqtSignal(str)

    def run(self):
        camera = None
        try:
            import cv2
            import face_recognition

            camera = cv2.VideoCapture(0)
            if not camera.isOpened():
                raise RuntimeError("Cannot open camera 0. Check camera permissions or other apps.")
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            self.status.emit("Camera on. Look at it alone; enrollment starts in 5 seconds.")
            start = time.monotonic()
            owners = []
            while not self.isInterruptionRequested():
                ok, frame = camera.read()
                if not ok:
                    raise RuntimeError("The camera stopped responding.")
                now = time.monotonic()
                if now - start < 5:
                    self.msleep(100)
                    continue
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                encodings = face_recognition.face_encodings(rgb)
                if len(owners) < 5:
                    if now - start > 35:
                        raise RuntimeError("Enrollment timed out. Face the camera alone and try again.")
                    if len(encodings) == 1:
                        if owners and not any(face_recognition.compare_faces(owners, encodings[0], tolerance=0.5)):
                            owners.clear()
                        owners.append(encodings[0])
                        self.status.emit(f"Learning your face: {len(owners)}/5")
                        if len(owners) == 5:
                            self.status.emit("Camera guard is on. Your face was learned for this session.")
                    else:
                        owners.clear()
                        self.status.emit("Enrollment needs exactly one visible face. Look at the camera alone.")
                else:
                    owner = any(any(face_recognition.compare_faces(owners, face, tolerance=0.5))
                                for face in encodings)
                    self.presence.emit("owner" if owner else "unknown" if encodings else "empty")
                self.msleep(350)
        except (ImportError, SystemExit):
            self.failed.emit("Install camera support: .venv/bin/pip install -r requirements-camera.txt")
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if camera is not None:
                camera.release()


class GuardDialog(QDialog):
    cancelled = pyqtSignal()

    def reject(self):
        self.cancelled.emit()
        super().reject()


class CameraGuard(QObject):
    def __init__(self, buddy):
        super().__init__(buddy)
        self.buddy = buddy
        self.worker = None
        self.policy = GuardPolicy()
        self.active = False
        self.dialog = GuardDialog(buddy)
        self.dialog.setWindowTitle("Buddy camera guard")
        self.dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        layout = QVBoxLayout(self.dialog)
        self.label = QLabel()
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        cancel = QPushButton("Cancel shutdown / turn off camera guard")
        cancel.clicked.connect(self.stop)
        layout.addWidget(cancel)
        self.dialog.cancelled.connect(self.stop)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.tick)
        self.power = QProcess(self)
        self.power.errorOccurred.connect(lambda _: self.notice("Shutdown could not start."))
        self.power.finished.connect(self.power_finished)

    def notice(self, text):
        self.buddy.say(text, 8000)

    def start(self):
        if self.worker and self.worker.isRunning():
            return
        if (not self.buddy.listen or not self.buddy.listener.isRunning()
                or self.buddy.voice_state != "listening" or self.buddy.on_call()):
            self.notice("Enable voice listening and wait until it is ready before starting camera guard.")
            return
        result = QMessageBox.question(
            self.buddy, "Enable camera guard",
            "Sit alone facing the camera to enroll your face. Camera processing stays on this computer; "
            "your face template is kept only until the guard stops.\n\n"
            "If someone else appears without you, Buddy asks 'Who are you?' They have 20 seconds to "
            "say 'kamal is great', followed by a cancellable 30-second shutdown countdown. "
            "Shutdown can lose unsaved work. Face matching can make mistakes and is not a security lock.\n\n"
            "Enable camera guard now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if result != QMessageBox.StandardButton.Yes:
            return
        self.policy = GuardPolicy()
        self.active = True
        self.worker = CameraThread(self)
        self.worker.presence.connect(self.observe)
        self.worker.status.connect(self.status)
        self.worker.failed.connect(self.failure)
        self.worker.start()
        self.label.setText("Starting camera…")
        self.dialog.show()
        self.timer.start()

    def status(self, text):
        if self.active:
            self.label.setText(text)

    def failure(self, text):
        if self.active:
            self.stop()
            self.notice("Camera guard stopped: " + text)

    def stop(self):
        self.active = False
        self.timer.stop()
        self.policy.reset()
        self.dialog.hide()
        if self.worker:
            self.worker.requestInterruption()
        # No OS shutdown is scheduled: the cancellable deadline lives in our timer.

    def observe(self, presence):
        if not self.active:
            return
        before = self.policy.state
        self.policy.observe(presence, time.monotonic())
        if self.policy.state == "challenge" and before != "challenge":
            self.buddy.voice_reply("Who are you?")
            self.dialog.show()
            self.dialog.raise_()
        elif self.policy.state == "watching":
            self.label.setText("Camera guard on. " + ("Owner recognized." if presence == "owner"
                                                     else "No challenge pending."))

    def answer(self, text):
        if not self.active or self.policy.state not in ("challenge", "countdown"):
            return False
        if self.policy.answer(text):
            self.label.setText("Phrase accepted. Guest allowed until the camera is empty for 5 seconds.")
            self.buddy.voice_reply("Welcome. Shutdown cancelled.")
        else:
            self.buddy.voice_reply("That is not the correct phrase. Please try again.")
        return True

    def tick(self):
        if not self.active:
            return
        buddy = self.buddy
        if (not buddy.listen or not buddy.listener.isRunning() or buddy.on_call()
                or buddy.voice_state.startswith("error") or buddy.voice_state == "paused: reconnecting the microphone"):
            self.failure("Voice listening is unavailable; no shutdown will happen.")
            return
        now = time.monotonic()
        state = self.policy.tick(now)
        if state == "stale":
            if self.policy.last_frame is not None:
                self.failure("Camera frames are delayed; no shutdown will happen.")
            return
        if state == "poweroff":
            self.stop()
            self.power.start("systemctl", ["--no-ask-password", "poweroff"])
        elif state in ("challenge", "countdown"):
            seconds = max(0, math.ceil(self.policy.deadline - now))
            self.label.setText(f"Who are you? Waiting for your spoken response: {seconds}s."
                                if state == "challenge" else
                                f"No correct phrase heard. Computer shuts down in {seconds}s. Save your work!")
            # Keep taking direct responses throughout both deadlines, without a wake phrase.
            if not buddy.speaker.speaking:
                buddy.listener.follow_up()

    def power_finished(self, code, _status):
        if code:
            self.notice("Shutdown was refused by the system. Camera guard is off.")
