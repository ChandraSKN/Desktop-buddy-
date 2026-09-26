"""Record a meeting: your microphone and what the other people say, as two files.

PipeWire gives us both sides without any virtual devices: `pw-record` on the default
source is your mic, and on the default sink with `stream.capture.sink=true` it's the
speaker/headset output, i.e. the other participants. Keeping them separate lets the
transcript say "You" vs "Others" and drop the echo of their voices in your mic.

Each recording is a folder under RECORDINGS with meta.json; the processing pipeline
(transcribe → minutes) is driven by which files exist there, so it can resume after a
restart."""

import json
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path

TRACKS = {
    "mic.wav": ["--target", "@DEFAULT_AUDIO_SOURCE@"],
    "others.wav": ["-P", "{ stream.capture.sink=true }", "--target", "@DEFAULT_AUDIO_SINK@"],
}
OUR_BINARIES = {"pw-record"}


def folder_name(start, title):
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()[:60] or "Meeting"
    return f"{start.astimezone().strftime('%Y-%m-%d %H%M')} {safe}"


class Recorder:
    def __init__(self, root):
        self.root = Path(root)
        self.folder = None
        self.procs = []
        self.started = None

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
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE))
        return self.folder

    def failed(self):
        """A track that exited on its own (device missing, PipeWire down): its error text."""
        for proc in self.procs:
            if proc.poll() is not None:
                return (proc.stderr.read() or b"").decode(errors="replace").strip() or "recorder stopped"
        return None

    def stop(self, now=None):
        """Stop and finalize both files. Returns the recording folder."""
        for proc in self.procs:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)        # pw-record writes the WAV header on exit
        for proc in self.procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.procs = []
        meta_path = self.folder / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["recorded_to"] = (now or datetime.now(UTC)).isoformat()
        meta_path.write_text(json.dumps(meta, indent=1))
        return self.folder


def other_apps_using_mic():
    """Names of apps (other than our own recorder) currently capturing audio input."""
    try:
        out = subprocess.run(["pw-dump"], capture_output=True, timeout=5, check=True).stdout
        objects = json.loads(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    apps = []
    for obj in objects:
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Stream/Input/Audio":
            continue
        if props.get("stream.capture.sink") in (True, "true"):
            continue                                  # recording speakers, not the mic
        binary = props.get("application.process.binary", "")
        if binary in OUR_BINARIES:
            continue
        apps.append(props.get("application.name") or binary or "an app")
    return apps
