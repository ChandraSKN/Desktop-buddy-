"""The minutes flow in the real window, with a fake recorder and a fake Claude."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from deskbuddy.services import minutes as minutes_mod
from deskbuddy.services.minutes import ActionItem, Minutes
from deskbuddy.services.reminders import Event
from deskbuddy.ui import minutes_bar
from deskbuddy.ui.buddy_window import Buddy

WORDS = "we agreed to ship the new dashboard next friday and ravi will write the release notes " * 3


class FakeRecorder:
    """Writes a transcript instead of audio, so the pipeline runs without a microphone."""

    def __init__(self, root):
        self.root, self.recording, self.started, self.folder = Path(root), False, None, None

    def start(self, title, event=None, now=None):
        self.started = datetime.now(UTC)
        self.folder = self.root / f"rec {title}"
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "meta.json").write_text(json.dumps(
            {"title": title, "recorded_from": self.started.isoformat()}))
        (self.folder / "mic.wav").write_bytes(b"RIFF")
        self.recording = True

    def failed(self):
        return None

    def stop(self, now=None):
        self.recording = False
        meta = json.loads((self.folder / "meta.json").read_text())
        meta["recorded_to"] = datetime.now(UTC).isoformat()
        (self.folder / "meta.json").write_text(json.dumps(meta))
        (self.folder / "transcript.json").write_text(json.dumps(
            [{"start": 0, "end": 30, "speaker": "Others", "text": WORDS}]))
        return self.folder


def fake_minutes(segments, title, preferences=(), client=None):
    return Minutes(summary=f"Summary of {title}.", decisions=["Ship next Friday"],
                   action_items=[ActionItem(owner="Ravi", task="Write the release notes", due="")],
                   discussion=[], open_questions=[])


@pytest.fixture
def buddy(qtbot, monkeypatch, tmp_path):
    monkeypatch.setattr(Buddy, "sync_calendar", lambda self: None)
    monkeypatch.setattr(minutes_bar, "Recorder", FakeRecorder)
    monkeypatch.setattr(minutes_bar, "MINUTES_DIR", str(tmp_path / "minutes"))
    monkeypatch.setattr(minutes_bar, "RECORDINGS_DIR", str(tmp_path / "recordings"))
    monkeypatch.setattr(minutes_mod, "write_minutes", fake_minutes)
    monkeypatch.setattr(minutes_bar, "other_apps_using_mic", lambda: [])
    monkeypatch.setattr("PyQt6.QtGui.QDesktopServices.openUrl", lambda url: True)
    b = Buddy()
    qtbot.addWidget(b)
    b.calendar_timer.stop()
    b.reminder_timer.stop()
    return b


def meeting(minutes_from_now=0):
    start = datetime.now(UTC) + timedelta(minutes=minutes_from_now)
    return Event("k1", start, "Dashboard sync", start + timedelta(minutes=30), "https://meet.google.com/x")


def test_joining_through_buddy_offers_minutes_once(buddy):
    ev = meeting()
    buddy.events = [ev]
    buddy.join_meeting(ev)
    assert buddy.minutes.state == "offer" and "Dashboard sync" in buddy.minutes.label.text()
    buddy.minutes.hide()
    buddy.join_meeting(ev)
    assert not buddy.minutes.isVisible()                      # asked once per meeting


def test_record_stop_and_get_minutes(buddy, qtbot, tmp_path):
    ev = meeting()
    buddy.events = [ev]
    buddy.join_meeting(ev)
    buddy.minutes._primary()                                  # Start
    buddy.minutes.tick()
    assert buddy.minutes.state == "recording"
    buddy.minutes._primary()                                  # Stop
    qtbot.waitUntil(lambda: buddy.minutes.state in ("done", "error"), timeout=5000)
    assert buddy.minutes.state == "done", buddy.minutes.note.text()

    md = Path(buddy.minutes.result_path).read_text()
    assert "# Minutes: Dashboard sync" in md and "**Ravi**: Write the release notes" in md
    assert buddy.memory.search_minutes("dashboard")[0].title == "Dashboard sync"
    assert not list((tmp_path / "recordings").glob("*/mic.wav"))     # audio deleted afterwards


def test_recording_stops_itself_after_the_meeting(buddy, qtbot):
    ev = meeting(-60)                                          # ended 30 min ago
    buddy.minutes.start(ev)
    buddy.minutes.tick()
    assert not buddy.minutes.recorder.recording
    qtbot.waitUntil(lambda: buddy.minutes.state == "done", timeout=5000)


def test_other_app_on_the_mic_during_a_meeting_offers_minutes(buddy, monkeypatch):
    monkeypatch.setattr(minutes_bar, "other_apps_using_mic", lambda: ["Microsoft Teams"])
    buddy.events = [meeting(-2)]
    buddy.minutes.tick()
    assert buddy.minutes.state == "offer"


def test_no_offer_without_a_meeting_on(buddy, monkeypatch):
    monkeypatch.setattr(minutes_bar, "other_apps_using_mic", lambda: ["Firefox"])
    buddy.events = [meeting(120)]                              # in two hours
    buddy.minutes.tick()
    assert not buddy.minutes.isVisible()


def test_unfinished_recordings_are_picked_up_at_start(buddy, qtbot, tmp_path):
    folder = tmp_path / "recordings" / "2026-09-27 2030 Old sync"
    folder.mkdir(parents=True)
    now = datetime.now(UTC)
    (folder / "meta.json").write_text(json.dumps({"title": "Old sync", "recorded_from": now.isoformat(),
                                                  "recorded_to": now.isoformat()}))
    (folder / "transcript.json").write_text(json.dumps(
        [{"start": 0, "end": 30, "speaker": "Others", "text": WORDS}]))
    buddy.minutes.resume_pending()
    qtbot.waitUntil(lambda: buddy.minutes.state == "done", timeout=5000)
    assert (folder / "minutes.json").exists()


def test_too_little_speech_gives_no_minutes(buddy, qtbot, tmp_path):
    folder = tmp_path / "recordings" / "quiet"
    folder.mkdir(parents=True)
    now = datetime.now(UTC).isoformat()
    (folder / "meta.json").write_text(json.dumps({"title": "Quiet", "recorded_from": now, "recorded_to": now}))
    (folder / "transcript.json").write_text(json.dumps([{"start": 0, "end": 1, "speaker": "You", "text": "hi"}]))
    buddy.minutes.resume_pending()
    qtbot.waitUntil(lambda: buddy.minutes.state == "error", timeout=5000)
    assert "Hardly anything" in buddy.minutes.note.text()
