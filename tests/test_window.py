"""Smoke tests of the real window, rendered off-screen."""

import time
from datetime import UTC, datetime, timedelta

import pytest

from deskbuddy import config
from deskbuddy.character import chair, model3d
from deskbuddy.ui.buddy_window import Buddy


@pytest.fixture
def buddy(qtbot, monkeypatch):
    monkeypatch.setattr(Buddy, "sync_calendar", lambda self: None)
    b = Buddy()
    qtbot.addWidget(b)
    b.show()
    b.calendar_timer.stop()
    b.reminder_timer.stop()
    return b


def test_draws_the_character(buddy):
    buddy.render_frame()
    img = buddy._frame
    assert img.width() == config.WIN_W
    opaque = sum(img.pixelColor(x, y).alpha() > 0
                 for x in range(0, img.width(), 5) for y in range(0, img.height(), 5))
    assert opaque > 200


def test_a_stale_hover_does_not_freeze_him(buddy):
    """XWayland may never send a leave event; hovering must lapse on its own."""
    buddy.hovered = True
    buddy.last_pointer = time.monotonic()
    assert buddy.frozen()
    buddy.last_pointer = time.monotonic() - config.HOVER_HOLD - 1
    assert not buddy.frozen()


@pytest.mark.skipif(not chair.available(), reason="sitting frames not rendered")
def test_sits_after_no_clicks_and_a_click_gets_him_up(buddy, qtbot):
    char_x = buddy.char_x()
    buddy.last_click = time.monotonic() - config.SIT_AFTER - 1
    buddy.state, buddy.state_until = "idle", time.monotonic() + 60
    qtbot.waitUntil(lambda: buddy.chair is not None, timeout=2000)
    assert buddy.width() == config.WIN_W + config.CHAIR_PAD
    assert buddy.char_x() == char_x                       # he doesn't jump when the window grows
    qtbot.waitUntil(lambda: buddy.chair.phase == "seated", timeout=5000)

    buddy.stand_up()
    assert buddy.chair.leaving
    qtbot.waitUntil(lambda: buddy.chair is None, timeout=5000)
    assert buddy.width() == config.WIN_W


def test_coming_back_greets_you_and_counts_as_a_break(buddy):
    buddy.welcome_back(20 * 60)
    assert "20 min" in buddy.bubble.text
    left = buddy.schedule.next_wellness - datetime.now(UTC)
    assert timedelta(minutes=59) < left <= timedelta(hours=1)


def test_without_an_api_key_the_chat_explains_how_to_connect(buddy, monkeypatch):
    from deskbuddy.services import agent as agent_mod
    monkeypatch.setattr(agent_mod, "available", lambda: False)
    buddy.open_chat()
    buddy.chat.new_chat()
    assert "API key" in buddy.chat.log.toPlainText()
    buddy.chat.input.setPlainText("hello")
    buddy.chat.send()
    assert buddy.chat.worker is None                          # nothing was sent anywhere
    buddy.chat.close()
    assert not buddy.busy


def test_chat_streams_a_reply_from_the_agent(buddy, qtbot, monkeypatch):
    from deskbuddy.services import agent as agent_mod

    class FakeAgent:
        def send(self, text, on_text, on_action, spoken=False, language="en"):
            on_text("Your next meeting ")
            on_text("is at 15:00.")
            return "Your next meeting is at 15:00."

    monkeypatch.setattr(agent_mod, "available", lambda: True)
    buddy.chat.make_agent = FakeAgent
    buddy.open_chat()
    buddy.chat.input.setPlainText("what's next?")
    buddy.chat.send()
    qtbot.waitUntil(lambda: not buddy.chat.worker.isRunning(), timeout=3000)
    qtbot.waitUntil(lambda: "15:00" in buddy.chat.log.toPlainText(), timeout=3000)
    assert "what's next?" in buddy.chat.log.toPlainText()


def test_a_reminder_the_assistant_set_fires_on_screen(buddy):
    buddy.memory.add_reminder("Email Ravi", datetime.now(UTC) - timedelta(seconds=1))
    buddy.check_reminders()
    assert "Email Ravi" in buddy.bubble.text
    assert buddy.memory.pending_reminders() == []


def test_heard_speech_goes_to_the_assistant_and_the_reply_is_spoken(buddy, qtbot, monkeypatch):
    from deskbuddy.services import agent as agent_mod

    class FakeAgent:
        def send(self, text, on_text, on_action, spoken=False, language="en"):
            assert spoken
            return f"You asked: {text}"

    spoken = []
    monkeypatch.setattr(agent_mod, "available", lambda: True)
    monkeypatch.setattr(buddy.speaker, "say", lambda text: spoken.append(text) or True)
    buddy.chat.make_agent = FakeAgent
    buddy.on_heard("what's my next meeting")
    qtbot.waitUntil(lambda: bool(spoken), timeout=3000)
    assert spoken == ["You asked: what's my next meeting"]
    assert "🎤 what's my next meeting" in buddy.chat.log.toPlainText()


def test_he_stands_still_and_shows_his_mouth_while_talking(buddy, monkeypatch):
    from deskbuddy.character import model3d
    if not model3d.can_talk():
        pytest.skip("talk frames not rendered")
    monkeypatch.setattr(buddy.speaker, "mouth", lambda: 3)
    assert buddy.frozen()
    buddy.render_frame()
    open_mouth = buddy._frame.copy()
    monkeypatch.setattr(buddy.speaker, "mouth", lambda: None)
    buddy.render_frame()
    assert open_mouth != buddy._frame


def test_speaking_can_be_turned_off(buddy, monkeypatch):
    said = []
    monkeypatch.setattr(buddy.speaker, "say", lambda text: said.append(text))
    monkeypatch.setattr(buddy.settings, "setValue", lambda *a: None)      # keep your real settings
    buddy.speak = True
    buddy.toggle_speak()
    buddy.voice_reply("hello")
    assert said == [] and "hello" in buddy.bubble.text


@pytest.mark.skipif(not all(model3d.can_do(a) for a in ("film", "write")), reason="activity frames not rendered")
def test_he_films_while_recording_and_writes_while_the_minutes_are_made(buddy, monkeypatch):
    assert buddy.activity() is None
    drawn = []
    real = model3d.draw_model
    monkeypatch.setattr(model3d, "draw_model", lambda *a, **k: drawn.append(a[10]) or real(*a, **k))
    monkeypatch.setattr(buddy.minutes.recorder, "procs", [object()])      # recording
    assert buddy.activity() == "film" and buddy.frozen()                   # stands still, no walking
    buddy.render_frame()
    monkeypatch.setattr(buddy.minutes.recorder, "procs", [])
    monkeypatch.setattr(buddy.minutes, "worker", object())               # minutes being written
    assert buddy.activity() == "write"
    buddy.render_frame()
    assert drawn == ["film", "write"]
    img = buddy._frame
    assert sum(img.pixelColor(x, y).alpha() > 0
               for x in range(0, img.width(), 5) for y in range(0, img.height(), 5)) > 200


@pytest.fixture
def arriving(qtbot, monkeypatch):
    from deskbuddy.ui import buddy_window
    monkeypatch.setattr(buddy_window, "ENTRANCE", True)
    monkeypatch.setattr(buddy_window, "ENTRANCE_DELAY", 0.0)
    monkeypatch.setattr(Buddy, "sync_calendar", lambda self: None)
    b = Buddy()
    qtbot.addWidget(b)
    b.show()
    b.calendar_timer.stop()
    b.reminder_timer.stop()
    return b


def _opaque_columns(img):
    return [x for x in range(img.width()) if any(img.pixelColor(x, y).alpha() > 0
                                                 for y in range(0, img.height(), 4))]


def test_he_walks_in_from_the_right_edge_then_greets_you(arriving, qtbot):
    b = arriving
    b.render_frame()
    assert not _opaque_columns(b._frame)                  # starts out of sight
    qtbot.waitUntil(lambda: b.entering is not None and 40 < b.entering < 100, timeout=3000)
    b.render_frame()
    cols = _opaque_columns(b._frame)
    assert cols and max(cols) <= b.screen_right - b.x()   # partly out, cut at the screen edge
    assert b.state == "walk" and not b.bubble.isVisible()
    qtbot.waitUntil(lambda: b.entering is None, timeout=4000)
    assert b.fx == b.max_x                                 # home, where he always stands
    qtbot.waitUntil(lambda: b.bubble.isVisible(), timeout=2000)
    assert any(word in b.bubble.text for word in ("Good", "Hi!"))
    assert b.wave_start is not None


def test_what_he_says_on_the_way_in_waits_for_the_greeting(arriving, qtbot):
    b = arriving
    b.say("Calendar connected. Next: Standup at 10:00", 6000)
    assert not b.bubble.isVisible() and b.held_bubbles
    b.arrive()
    qtbot.waitUntil(lambda: "Good" in b.bubble.text or "Hi!" in b.bubble.text, timeout=2000)
    qtbot.waitUntil(lambda: "Calendar connected" in b.bubble.text, timeout=7000)


def test_a_click_or_reminder_while_walking_in_brings_him_straight_home(arriving):
    arriving.stand_up()
    assert arriving.entering is None and arriving.state == "idle"


def test_greeting_follows_the_time_of_day():
    from deskbuddy.ui.buddy_window import greeting
    assert greeting(8).startswith("Good morning")
    assert greeting(14).startswith("Good afternoon")
    assert greeting(19).startswith("Good evening")
    assert "late" in greeting(1)


def test_blink_reaches_real_window_in_standing_and_seated_poses(buddy, monkeypatch):
    from deskbuddy.character.expression import BLINKS
    from deskbuddy.ui import buddy_window

    buddy.timer.stop()
    buddy.state = "idle"
    buddy.wave_start = None
    buddy.entering = None
    buddy.yaw = -0.22
    monkeypatch.setattr(buddy.speaker, "mouth", lambda: None)
    clock = [BLINKS[0] - 0.1]
    monkeypatch.setattr(buddy_window.time, "monotonic", lambda: clock[0])
    for seated in (False, True):
        buddy.chair = chair.ChairScene(0) if seated else None
        if seated:
            buddy.chair.phase = "seated"
            buddy.chair.start = BLINKS[0] - 0.1
        clock[0] = BLINKS[0] - 0.1
        buddy.render_frame()
        opened = buddy._frame.copy()
        clock[0] = BLINKS[0] + 0.18
        buddy.render_frame()
        assert buddy._frame != opened


def test_quit_hides_buddy_and_voice_return_restores_him(buddy, monkeypatch):
    monkeypatch.setattr(buddy.chat, "ask", lambda *a, **kw: pytest.fail("return must work without an agent"))
    buddy.say("hello")
    buddy.dismiss()
    assert not buddy.isVisible() and not buddy.bubble.isVisible()
    assert buddy.listener.enabled and buddy.listener.return_only
    buddy.on_wake()
    buddy.on_heard("open Firefox")
    buddy.say("a delayed reminder")
    assert not buddy.isVisible() and not buddy.bubble.isVisible()
    buddy.on_heard("Buddy come back")
    assert buddy.isVisible() and not buddy.dismissed
    assert not buddy.listener.return_only
    assert buddy.listener.enabled == buddy.listen
    assert buddy.bubble.text == "I'm back!"


def test_youtube_music_gives_way_to_meetings_and_pause(buddy, monkeypatch):
    monkeypatch.setattr(model3d, "can_do", lambda kind: True)
    monkeypatch.setattr(buddy.speaker, "mouth", lambda: None)
    buddy.music.playing = True
    buddy.on_music_changed(True)
    assert buddy.activity() == "music"
    buddy.paused = True
    assert buddy.activity() is None
    buddy.paused = False
    buddy.voice_state = "paused: on a call"
    assert buddy.activity() is None
    buddy.voice_state = "listening"
    monkeypatch.setattr(buddy.minutes.recorder, "procs", [object()])
    assert buddy.activity() == "film"
    monkeypatch.setattr(buddy.minutes.recorder, "procs", [])
    buddy.music.playing = False
    assert buddy.activity() is None


def test_lets_talk_enlarges_centres_and_goodbye_restores_buddy(buddy, monkeypatch):
    from PyQt6.QtGui import QGuiApplication
    said = []
    monkeypatch.setattr(buddy, "voice_reply", lambda text: said.append(text))
    buddy.dismiss()
    buddy.on_heard("Buddy let's talk")
    screen = QGuiApplication.primaryScreen().availableGeometry()
    assert buddy.talk_mode and buddy.isVisible()
    assert buddy.height() > config.WIN_H
    assert abs(buddy.geometry().center().x() - screen.center().x()) <= 1
    assert buddy.frozen() and said
    buddy.tick()
    assert abs(buddy.geometry().center().x() - screen.center().x()) <= 1
    buddy.on_heard("goodbye")
    assert not buddy.talk_mode
    assert buddy.size().width() == config.WIN_W
    assert buddy.size().height() == config.WIN_H


def test_record_the_meeting_by_voice_without_a_calendar_meeting(buddy, monkeypatch):
    rec, said, stopped, started = buddy.minutes.recorder, [], [], []
    monkeypatch.setattr(rec, "failed", lambda: None)
    monkeypatch.setattr(rec, "follow_call", lambda: [])
    rec.started = datetime.now(UTC)
    monkeypatch.setattr(rec, "start", lambda title, event=None: started.append((title, event)) or
                        rec.procs.append(object()))
    monkeypatch.setattr(buddy.minutes, "stop", lambda reason="": stopped.append(True) or rec.procs.clear())
    monkeypatch.setattr(buddy, "voice_reply", lambda text: said.append(text))
    buddy.events = []
    buddy.on_heard("record the meeting and write minutes of meeting")
    assert started == [("Meeting", None)]
    assert "Recording" in said[-1]
    buddy.on_heard("record the meeting")
    assert len(started) == 1 and "already" in said[-1]
    buddy.on_heard("stop recording")
    assert stopped and "writing the minutes" in said[-1]


def test_while_recording_he_listens_only_for_stop(buddy, monkeypatch):
    rec = buddy.minutes.recorder
    monkeypatch.setattr(rec, "failed", lambda: None)
    monkeypatch.setattr(rec, "follow_call", lambda: [])
    monkeypatch.setattr(rec, "stop", lambda now=None: rec.procs.clear())
    rec.procs.append(object())
    rec.started = datetime.now(UTC)
    buddy.check_reminders()
    assert buddy.listener.stop_only and not buddy.listener.hold
    rec.procs.clear()


def test_assistant_can_start_and_stop_recording(buddy, qtbot, monkeypatch):
    calls = []
    monkeypatch.setattr(buddy, "record_meeting", lambda spoken=True: calls.append(("start", spoken)))
    monkeypatch.setattr(buddy, "stop_recording", lambda spoken=True: calls.append(("stop", spoken)))
    box = buddy.make_agent().toolbox
    box.run("start_recording", {})
    box.run("stop_recording", {})
    qtbot.waitUntil(lambda: len(calls) == 2, timeout=2000)
    assert calls == [("start", False), ("stop", False)]
