"""Who is speaking, from Teams' highlight on the screen recording; and the saved media."""

import subprocess
import wave

import cv2
import numpy as np
import pytest

from deskbuddy.services import media, screen
from deskbuddy.services import speakers as sp

NAMES = ("Rahul Sharma", "Priya Reddy", "Chandra S K N", "Anil Kumar (Guest)")


def teams_frame(active, border=(245, 133, 127)):
    """A 2x2 Teams gallery; `active` has the speaking border. Includes a solid blue button
    and a blue avatar touching a tile, which must not count."""
    f = np.full((720, 1280, 3), (36, 31, 31), np.uint8)
    cv2.rectangle(f, (20, 650), (90, 690), (199, 95, 91), -1)
    cv2.circle(f, (1200, 40), 20, (230, 120, 60), -1)
    tiles = [(40, 60, 580, 290), (660, 60, 580, 290), (40, 370, 580, 270), (660, 370, 580, 270)]
    for i, (x, y, w, h) in enumerate(tiles):
        cv2.rectangle(f, (x, y), (x + w, y + h), (60, 55, 55), -1)
        cv2.circle(f, (x + w // 2, y + h // 2), 50, (180, 140, 90), -1)
        cv2.rectangle(f, (x + 8, y + h - 34), (x + 8 + len(NAMES[i]) * 11, y + h - 8), (20, 20, 20), -1)
        cv2.putText(f, NAMES[i], (x + 12, y + h - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1,
                    cv2.LINE_AA)
        if i == active:
            cv2.rectangle(f, (x, y), (x + w, y + h), border, 3)
    return f


@pytest.fixture(scope="module")
def ocr():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR()


@pytest.mark.parametrize("border", [(245, 133, 127), (199, 95, 91), (212, 120, 0)],
                         ids=["dark-theme", "light-theme", "blue"])
def test_the_highlighted_tile_and_its_name_are_found(ocr, border):
    got = []
    for active in range(4):
        frame = teams_frame(active, border)
        boxes = sp.highlight_boxes(frame)
        got.append(sp.read_name(frame, boxes[0], ocr) if boxes else None)
    assert got == ["Rahul Sharma", "Priya Reddy", "Chandra S K N", "Anil Kumar"]


def test_no_highlight_when_nobody_speaks():
    assert sp.highlight_boxes(teams_frame(-1)) == []


def test_names_are_cleaned_and_spellings_unified():
    assert sp.clean_name(["Anil Kumar (External)", "🎤"]) == "Anil Kumar"
    assert sp.clean_name(["|", "2"]) == ""
    spans = sp.timeline([(0, "Rahul Sharma"), (1, "Rahul Sharma"), (2, "Rahu1 Sharma"),
                         (3, ""), (4, "Rahul Sharma"), (9, "Priya Reddy")])
    assert spans == [{"start": 0, "end": 5, "name": "Rahul Sharma"},
                     {"start": 9, "end": 10, "name": "Priya Reddy"}]


def test_others_segments_get_the_highlighted_name():
    spans = [{"start": 0, "end": 10, "name": "Rahul Sharma"}, {"start": 10, "end": 20, "name": "Priya Reddy"}]
    segments = [{"start": 5, "end": 9, "speaker": "Others", "text": "a"},
                {"start": 13, "end": 18, "speaker": "Others", "text": "b"},
                {"start": 13, "end": 18, "speaker": "You", "text": "c"},
                {"start": 40, "end": 45, "speaker": "Others", "text": "d"}]
    named = sp.name_segments(segments, spans, offset=2.0)      # the video started 2 s after the audio
    assert [s.get("name") for s in named] == ["Rahul Sharma", "Priya Reddy", None, None]


def _wav(path, seconds, tone):
    rate = 16000
    t = np.arange(int(rate * seconds)) / rate
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes((np.sin(t * tone * 6.28) * 6000).astype(np.int16).tobytes())


def test_finalize_keeps_audio_and_video_together(tmp_path):
    _wav(tmp_path / "mic.wav", 3, 300)
    _wav(tmp_path / "others.wav", 3, 500)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=5:duration=2",
                    "-c:v", "libx264", str(tmp_path / "screen.mkv")], check=True)
    kept = media.finalize(tmp_path, screen_offset=1.0)
    assert [p.name for p in kept] == ["audio.ogg", "recording.mkv"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["audio.ogg", "recording.mkv"]
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                            str(tmp_path / "recording.mkv")], capture_output=True, text=True).stdout.split()
    assert sorted(probe) == ["audio", "video"]
    assert media.finalize(tmp_path) == kept                   # running again changes nothing


def test_screen_pipeline_reads_the_portal_stream():
    args = screen.pipeline(42, "/tmp/x.mkv", 7)
    assert args[:4] == ["gst-launch-1.0", "-e", "pipewiresrc", "fd=7"] and "path=42" in args
    assert "location=/tmp/x.mkv" in args


def test_finalize_can_keep_sources_for_transcription(tmp_path):
    _wav(tmp_path / "mic.wav", 3, 300)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=5:duration=2",
                    "-c:v", "libx264", str(tmp_path / "screen.mkv")], check=True)
    media.finalize(tmp_path, screen_offset=1, keep_raw=True)
    assert all((tmp_path / name).exists() for name in ("mic.wav", "screen.mkv", "audio.ogg", "recording.mkv"))
    media.finalize(tmp_path, screen_offset=1)
    assert not (tmp_path / "mic.wav").exists() and not (tmp_path / "screen.mkv").exists()
