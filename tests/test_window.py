"""Smoke tests of the real window, rendered off-screen."""

import time
from datetime import UTC, datetime, timedelta

import pytest

from deskbuddy import config
from deskbuddy.character import chair
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
