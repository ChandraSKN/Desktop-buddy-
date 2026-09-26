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
