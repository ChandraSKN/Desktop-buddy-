"""Buddy's ears and voice in the app: the listening thread and speech playback.

Listening pauses by itself while Buddy is speaking (so he doesn't hear himself), while
minutes are being recorded, and while another app is using the microphone (you're on a
call, and other people saying "buddy" shouldn't wake him)."""

import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QObject, QProcess, QThread, pyqtSignal

from ..services.listener import CONVERSATION, FRAME, RATE, Endpointer, WakeListener
from ..services.recorder import ignore_debug_signal, other_apps_using_mic
from ..services.speech import MOUTH_FPS, TELUGU_VOICE, Voice, clean_for_speech
from ..services.telugu import has_telugu

CALL_CHECK_EVERY = 5.0


class ListenerThread(QThread):
    wake = pyqtSignal()
    heard = pyqtSignal(str, str)       # command text (English), language it was spoken in
    awaiting = pyqtSignal()            # listening for a follow-up question, no "Hey Buddy" needed
    status = pyqtSignal(str)           # "loading", "listening", "paused: …", "error: …"

    def __init__(self):
        super().__init__()
        self.enabled = True            # the menu toggle
        self.hold = False              # set while Buddy speaks or records minutes
        self._stop = False
        self._push_to_talk = False
        self._follow_up = False
        self.language = "en"           # of the last command
        self.proc = None

    # called from the UI thread
    def push_to_talk(self):
        self._push_to_talk = True

    def follow_up(self):
        """Buddy has answered: take the next thing said (once he's quiet) as a question."""
        self._follow_up = True

    def stop(self):
        self._stop = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.wait(3000)

    def run(self):
        self.status.emit("loading")
        try:
            from faster_whisper import WhisperModel
            from faster_whisper.vad import VadOptions, get_speech_timestamps
            quick_model = WhisperModel("tiny.en", device="cpu", compute_type="int8", cpu_threads=2)
            # "base" answers in ~0.9 s vs ~2.2 s for "small" on this kind of laptop; commands are
            # short and Claude allows for misheard words. Minutes keep "small" (not urgent).
            full_model = WhisperModel("base", device="cpu", compute_type="int8", cpu_threads=4)
        except Exception as exc:
            self.status.emit(f"error: {exc}")
            return

        def text_of(model, audio, **kw):
            segments, info = model.transcribe(audio, beam_size=1, temperature=0.0,
                                              condition_on_previous_text=False, **kw)
            text = " ".join(s.text.strip() for s in segments
                            if s.no_speech_prob < 0.6 and s.compression_ratio < 2.4)
            return text, info.language, info.language_probability

        translator = []                              # "small", loaded on first non-English command

        def command_text(audio):
            """English text of a command, and the language it was spoken in. English goes
            through the fast model; Telugu (or mixed, e.g. "Firefox open cheyyi") is
            translated to English by "small": on Telugu, "base" gives gibberish and "small"
            loops when writing Telugu script, but translates well (measured)."""
            text, language, confidence = text_of(full_model, audio, multilingual=True)
            if language == "en" and confidence >= 0.6:
                self.language = "en"
                return text
            if not translator:
                translator.append(WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=4))
            translated, _, _ = text_of(translator[0], audio, task="translate")
            self.language = "te"                     # Hindi/Tamil/… labels are Telugu here
            return translated or text

        wake = WakeListener(lambda a: text_of(quick_model, a, language="en")[0], command_text)
        vad = VadOptions(min_speech_duration_ms=250)
        failures = []
        while not self._stop:
            self._listen(wake, vad, get_speech_timestamps)
            if self._stop:
                break
            # the mic stream ended by itself (device unplugged, headset off, PipeWire restart):
            # start it again, unless it keeps failing
            now = time.monotonic()
            failures = [t for t in failures if now - t < 60] + [now]
            if len(failures) >= 5:
                self.status.emit("error: the microphone keeps stopping")
                return
            self.status.emit("paused: reconnecting the microphone")
            time.sleep(2)

    def _listen(self, wake, vad, get_speech_timestamps):
        """Read the mic until it stops or we're told to stop."""
        endpointer = Endpointer()
        self.proc = subprocess.Popen(["pw-record", "--rate", str(RATE), "--channels", "1",
                                      "--format", "s16", "-"],
                                     stdout=subprocess.PIPE,          # its errors go to the log
                                     preexec_fn=ignore_debug_signal)
        next_call_check, in_call, last_status = 0.0, False, None
        while not self._stop:
            chunk = self.proc.stdout.read(FRAME * 2)
            if len(chunk) < FRAME * 2:
                break
            now = time.monotonic()
            if now >= next_call_check:
                next_call_check = now + CALL_CHECK_EVERY
                in_call = bool(other_apps_using_mic())
            if self._push_to_talk:
                self._push_to_talk = False
                wake.expect_command(now)
            if self._follow_up and not self.hold and self.enabled:
                self._follow_up = False
                wake.expect_command(now, CONVERSATION)
                self.awaiting.emit()
            reason = ("off" if not self.enabled else "busy" if self.hold
                      else "on a call" if in_call and now >= wake.awaiting_until else None)
            state = f"paused: {reason}" if reason else "listening"
            if state != last_status:
                self.status.emit(state)
                last_status = state
            if reason:
                endpointer = Endpointer()                # drop anything half-heard
                continue
            utterance = endpointer.feed(np.frombuffer(chunk, dtype=np.int16))
            if utterance is None or not get_speech_timestamps(utterance, vad):
                continue
            event = wake.on_utterance(utterance, time.monotonic())
            if event == ("wake",):
                self.wake.emit()
            elif event and event[0] == "command":
                self.heard.emit(event[1], self.language)
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(2)


class SynthThread(QThread):
    ready = pyqtSignal(str, list, float)     # wav path, mouth levels, seconds
    failed = pyqtSignal(str)

    def __init__(self, voice, text, path):
        super().__init__()
        self.voice, self.text, self.path = voice, text, path

    def run(self):
        try:
            levels, seconds = self.voice.synthesize(self.text, self.path)
            self.ready.emit(str(self.path), levels, seconds)
        except Exception as exc:              # no voice: he just shows the text
            self.failed.emit(str(exc))


class Speaker(QObject):
    """Say text out loud and report the mouth opening while doing it."""

    started = pyqtSignal()
    finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.voice = Voice()                         # English
        self.telugu = Voice(TELUGU_VOICE)
        self.levels, self.start = [], 0.0
        self.proc = QProcess(self)
        self.proc.finished.connect(self._done)
        self.synth = None
        self.dir = Path(tempfile.mkdtemp(prefix="buddy-voice-"))

    def available(self):
        return self.voice.available()

    @property
    def speaking(self):
        return self.proc.state() != QProcess.ProcessState.NotRunning or (
            self.synth is not None and self.synth.isRunning())

    def voice_for(self, text):
        """Telugu script → the Telugu voice (if installed), otherwise English."""
        return self.telugu if has_telugu(text) and self.telugu.available() else self.voice

    def say(self, text):
        text = clean_for_speech(text)
        voice = self.voice_for(text)
        if not text or not voice.available():
            return False
        self.stop()
        self.synth = SynthThread(voice, text, self.dir / "reply.wav")
        self.synth.ready.connect(self._play)
        self.synth.failed.connect(lambda _: self.finished.emit())
        self.started.emit()
        self.synth.start()
        return True

    def _play(self, path, levels, seconds):
        self.levels = levels
        self.start = time.monotonic()
        self.proc.start("pw-play", [path])

    def mouth(self):
        """Current mouth opening (0-3), or None when not speaking."""
        if self.proc.state() == QProcess.ProcessState.NotRunning:
            return None
        i = int((time.monotonic() - self.start) * MOUTH_FPS)
        return self.levels[i] if 0 <= i < len(self.levels) else 0

    def stop(self):
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.kill()
            self.proc.waitForFinished(1000)

    def _done(self, *_):
        self.levels = []
        self.finished.emit()
