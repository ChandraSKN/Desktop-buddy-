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
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, preexec_fn=ignore_debug_signal))
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
    # a stream's process id is on the PipeWire client that owns it
    client_pid = {obj.get("id"): ((obj.get("info") or {}).get("props") or {}).get("application.process.id")
                  for obj in objects if obj.get("type") == "PipeWire:Interface:Client"}
    apps = []
    for obj in objects:
        props = (obj.get("info") or {}).get("props") or {}
        if props.get("media.class") != "Stream/Input/Audio":
            continue
        if props.get("stream.capture.sink") in (True, "true"):
            continue                                  # recording speakers, not the mic
        pid = props.get("application.process.id") or client_pid.get(props.get("client.id"))
        if pid is not None and _started_by_us(pid):
            continue                                  # our own recorder / listener
        apps.append(props.get("application.name") or props.get("application.process.binary") or "an app")
    return apps
