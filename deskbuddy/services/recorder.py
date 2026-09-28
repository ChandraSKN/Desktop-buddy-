"""Record a meeting: your microphone and what the other people say, as two files.

PipeWire gives us both sides without any virtual devices: `pw-record` on the default
source is your mic, and on the default sink with `stream.capture.sink=true` it's the
speaker/headset output, i.e. the other participants. Keeping them separate lets the
transcript say "You" vs "Others" and drop the echo of their voices in your mic.

Each recording is a folder under RECORDINGS with meta.json; the processing pipeline
(transcribe → minutes) is driven by which files exist there, so it can resume after a
restart."""

import json
import os
import signal
import subprocess
import sys
import threading
import time
import wave
from datetime import UTC, datetime
from pathlib import Path

TRACKS = {
    "mic.wav": ["--target", "@DEFAULT_AUDIO_SOURCE@"],
    "others.wav": ["-P", "{ stream.capture.sink=true }", "--target", "@DEFAULT_AUDIO_SINK@"],
}


def _started_by_us(pid):
    """True if pid is a child of this process (our own recorder or listener). pw-record is
    really pw-cat, so matching on the program name isn't reliable."""
    try:
        with open(f"/proc/{int(pid)}/stat") as f:
            return int(f.read().rsplit(")", 1)[1].split()[1]) == os.getpid()
    except (OSError, ValueError, IndexError):
        return False


def ignore_debug_signal():
    """For child processes: `systemctl --user kill -s USR1 desktop-buddy` signals every process
    in the service, and pw-record would quit on it."""
    signal.signal(signal.SIGUSR1, signal.SIG_IGN)


def folder_name(start, title):
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()[:60] or "Meeting"
    return f"{start.astimezone().strftime('%Y-%m-%d %H%M')} {safe}"


class Recorder:
    def __init__(self, root, screen=False):
        self.root = Path(root)
        self.folder = None
        self.procs = []
        self.started = None
        self.screen = screen          # also record the screen (screen.py, its own process)
        self.screen_proc = None
        self.screen_state = {}        # latest line from screen.py: asking / recording / error
        self.meta_lock = threading.Lock()   # meta.json is also written from the screen watcher

    @property
    def recording(self):
        return bool(self.procs)

    def start(self, title, event=None, now=None):
        """Start both tracks. event: the calendar Event, if we know which meeting it is."""
        if self.recording:
            raise RuntimeError("already recording")
        self.started = now or datetime.now(UTC)
        self.folder = self.root / folder_name(self.started, title)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.folder.chmod(0o700)
        meta = {"title": title, "recorded_from": self.started.isoformat()}
        if event is not None:
            meta.update(event_key=event.key, start=event.start.isoformat(), end=event.end.isoformat())
        (self.folder / "meta.json").write_text(json.dumps(meta, indent=1))
        for name, args in TRACKS.items():
            self.procs.append(subprocess.Popen(
                ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", *args,
                 str(self.folder / name)],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, preexec_fn=ignore_debug_signal))
        self.started_clock = time.time()
        if self.screen:
            self._start_screen()
        return self.folder

    def _start_screen(self):
        """GNOME asks what to share (once; then it's remembered). The audio doesn't wait:
        the video's start time is noted so the two can be lined up afterwards."""
        from ..config import ROOT
        self.screen_state = {"state": "starting"}
        try:
            self.screen_proc = subprocess.Popen(
                [sys.executable, "-m", "deskbuddy.services.screen", str(self.folder / "screen.mkv")],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                preexec_fn=ignore_debug_signal)
        except OSError as exc:
            self.screen_state = {"error": str(exc)}
            return
        threading.Thread(target=self._watch_screen, args=(self.screen_proc, self.folder,
                                                          self.started_clock), daemon=True).start()

    def _watch_screen(self, proc, folder, audio_clock):
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            self.screen_state = msg
            if "at" in msg:
                self._update_meta(folder, screen_offset=round(msg["at"] - audio_clock, 2))  # video starts this late
            if "error" in msg:
                self._update_meta(folder, screen_error=msg["error"])

    def _update_meta(self, folder, **values):
        with self.meta_lock:
            meta_path = folder / "meta.json"
            meta = json.loads(meta_path.read_text())
            meta.update(values)
            meta_path.write_text(json.dumps(meta, indent=1))

    def failed(self):
        """A track that exited on its own (device missing, PipeWire down): its error text."""
        for proc in self.procs:
            if proc.poll() is not None:
                return (proc.stderr.read() or b"").decode(errors="replace").strip() or "recorder stopped"
        return None

    def _track_nodes(self, objects):
        """{track name: our stream node} for the running recorder processes."""
        by_pid = {str(proc.pid): name for name, proc in zip(TRACKS, self.procs, strict=False)}
        pids = _stream_pids(objects)
        return {by_pid[str(pid)]: obj for obj in objects
                if str(pid := pids.get(obj.get("id"))) in by_pid
                and _props(obj).get("media.class") == "Stream/Input/Audio"}

    def follow_call(self, objects=None):
        """Point each track at the device the call app uses (your headset, not the default).
        Returns [(track, device name)] for the tracks it moved. Called every few seconds,
        because Bluetooth headsets swap devices when a call opens their mic."""
        if not self.recording:
            return []
        objects = _dump() if objects is None else objects
        want, ours = call_devices(objects), self._track_nodes(objects)
        moved = []
        for track, device in want.items():
            node = ours.get(track)
            if node is None:
                continue
            if device.get("id") in _peers(objects, node.get("id"), upstream=True):
                continue                     # already there (a sink's monitor ports are the sink's)
            serial = _props(device).get("object.serial")
            if serial is None:
                continue
            try:
                subprocess.run(["pw-metadata", str(node.get("id")), "target.object", str(serial)],
                               capture_output=True, timeout=5, check=True)
            except (OSError, subprocess.SubprocessError):
                continue
            moved.append((track, device_name(device)))
        if moved:
            self._note_devices(moved)
        return moved

    def _note_devices(self, moved):
        with self.meta_lock:
            meta_path = self.folder / "meta.json"
            meta = json.loads(meta_path.read_text())
            for track, name in moved:
                meta.setdefault("devices", {}).setdefault(track, []).append(name)
            meta_path.write_text(json.dumps(meta, indent=1))

    def stop(self, now=None):
        """Stop and finalize the files. Returns the recording folder."""
        screen, self.screen_proc = self.screen_proc, None
        if screen and screen.poll() is None:
            screen.send_signal(signal.SIGINT)
        for proc in self.procs:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)        # pw-record writes the WAV header on exit
        for proc in self.procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.procs = []
        if screen:
            try:
                screen.wait(timeout=20)              # the video file is finalized on the way out
            except subprocess.TimeoutExpired:
                screen.kill()
        self._update_meta(self.folder, recorded_to=(now or datetime.now(UTC)).isoformat(),
                          sound_seconds={name: round(loudness(self.folder / name)[1]) for name in TRACKS})
        return self.folder


def _dump():
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, timeout=5, check=True).stdout
        return json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def _props(obj):
    return (obj.get("info") or {}).get("props") or {}


def _stream_pids(objects):
    """{stream node id: process id}; a stream's process id may only be on its client."""
    client_pid = {obj.get("id"): _props(obj).get("application.process.id")
                  for obj in objects if obj.get("type") == "PipeWire:Interface:Client"}
    return {obj.get("id"): _props(obj).get("application.process.id") or client_pid.get(_props(obj).get("client.id"))
            for obj in objects if str(_props(obj).get("media.class", "")).startswith("Stream/")}


def _app_mic_streams(objects):
    """Other apps' mic streams (not ours, not speaker recordings): [(node, app name)]."""
    pids = _stream_pids(objects)
    found = []
    for obj in objects:
        props = _props(obj)
        if props.get("media.class") != "Stream/Input/Audio":
            continue
        if props.get("stream.capture.sink") in (True, "true"):
            continue                                  # recording speakers, not the mic
        pid = pids.get(obj.get("id"))
        if pid is not None and _started_by_us(pid):
            continue                                  # our own recorder / listener
        found.append((obj, props.get("application.name") or props.get("application.process.binary") or "an app"))
    return found


def other_apps_using_mic(objects=None):
    """Names of apps (other than our own recorder) currently capturing audio input."""
    return [name for _, name in _app_mic_streams(_dump() if objects is None else objects)]


def _peers(objects, node_id, upstream):
    """Node ids linked to node_id: the ones feeding it (upstream) or the ones it feeds."""
    mine, other = ("input-node-id", "output-node-id") if upstream else ("output-node-id", "input-node-id")
    return {(obj.get("info") or {}).get(other) for obj in objects
            if obj.get("type") == "PipeWire:Interface:Link" and (obj.get("info") or {}).get(mine) == node_id}


def _device(objects, ids, media_class):
    for obj in objects:
        if obj.get("id") in ids and str(_props(obj).get("media.class", "")).startswith(media_class):
            return obj
    return None


def call_devices(objects):
    """The mic and the speakers/headset the call app is actually using:
    {"mic.wav": device node, "others.wav": device node} (either may be missing).

    Call apps often pick a device themselves (your AirPods) while the system default is
    still the laptop, so recording "the default" can hear nothing of the meeting."""
    found = {}
    for stream, app in _app_mic_streams(objects):
        mic = _device(objects, _peers(objects, stream.get("id"), upstream=True), "Audio/Source")
        if mic is None:
            continue
        found["mic.wav"] = mic
        pids = _stream_pids(objects)
        for obj in objects:              # the same app's playback: where the others' voices go
            props = _props(obj)
            if props.get("media.class") != "Stream/Output/Audio":
                continue
            if (props.get("application.name") or props.get("application.process.binary") or "an app") != app:
                continue
            pid = pids.get(obj.get("id"))
            if pid is not None and _started_by_us(pid):
                continue
            sink = _device(objects, _peers(objects, obj.get("id"), upstream=False), "Audio/Sink")
            if sink is not None:
                found["others.wav"] = sink
                break
        break
    return found


def device_name(node):
    props = _props(node)
    return props.get("node.description") or props.get("node.nick") or props.get("node.name") or "?"


def loudness(path, threshold=0.01):
    """(seconds of audio, seconds with sound above the threshold RMS in 0.5 s blocks)."""
    import numpy as np
    try:
        with wave.open(str(path)) as w:
            rate, frames = w.getframerate(), w.readframes(w.getnframes())
    except (OSError, EOFError, wave.Error):
        return 0.0, 0.0
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768
    block = rate // 2
    n = len(audio) // block
    if not n:
        return len(audio) / rate, 0.0
    rms = np.sqrt((audio[:n * block].reshape(n, block) ** 2).mean(axis=1))
    return len(audio) / rate, float((rms > threshold).sum()) / 2
