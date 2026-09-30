"""Turn a finished recording's working files into what stays in the meeting's folder:

  audio.ogg       both sides of the call mixed (Opus, ~30 MB per hour)
  recording.mkv   the screen video with that audio, lined up (only if the screen was recorded)

The raw mic.wav / others.wav / screen.mkv are deleted once these exist. ffmpeg runs at low
priority with two threads, and the video isn't re-encoded, so this takes seconds."""

import subprocess
from pathlib import Path

from .transcribe import duration

TRACKS = ("mic.wav", "others.wav")


def _ffmpeg(args):
    subprocess.run(["nice", "-n", "15", "ffmpeg", "-v", "error", "-y", "-threads", "2", *args],
                   check=True, capture_output=True, timeout=3600)


def finalize(folder, screen_offset=0.0, *, keep_raw=False):
    """Returns the files kept. Safe to run again after a crash half-way."""
    folder = Path(folder)
    tracks = [folder / t for t in TRACKS if duration(folder / t) > 0]
    audio = folder / "audio.ogg"
    if tracks and not audio.exists():
        inputs = [arg for t in tracks for arg in ("-i", str(t))]
        mix = (["-filter_complex", f"amix=inputs={len(tracks)}:duration=longest:normalize=0"]
               if len(tracks) > 1 else [])
        tmp = folder / "audio.tmp.ogg"
        _ffmpeg([*inputs, *mix, "-c:a", "libopus", "-b:a", "48k", str(tmp)])
        tmp.replace(audio)
    screen = folder / "screen.mkv"
    video = folder / "recording.mkv"
    if screen.exists() and screen.stat().st_size > 0 and audio.exists() and not video.exists():
        tmp = folder / "recording.tmp.mkv"
        _ffmpeg(["-itsoffset", f"{max(0.0, screen_offset):.2f}", "-i", str(screen), "-i", str(audio),
                 "-map", "0:v", "-map", "1:a", "-c", "copy", str(tmp)])
        tmp.replace(video)
    if audio.exists() and not keep_raw:
        for t in tracks:
            t.unlink(missing_ok=True)
    if video.exists() and not keep_raw:
        screen.unlink(missing_ok=True)
    return [p for p in (audio, video) if p.exists()]
