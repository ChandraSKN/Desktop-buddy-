from datetime import UTC, datetime, timedelta

from deskbuddy.services.idle import AwayTracker
from deskbuddy.services.reminders import ReminderSchedule


def test_away_and_back():
    t = AwayTracker(300)
    assert t.update(10) is None
    assert t.update(301) == "away"
    assert t.update(900) is None                  # still away: no repeats
    assert t.update(1) == ("returned", 900)
    assert t.update(2) is None


def test_short_pauses_are_not_away():
    t = AwayTracker(300)
    assert [t.update(s) for s in (0, 120, 299, 3)] == [None] * 4


def test_being_away_counts_as_a_break():
    now = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
    schedule = ReminderSchedule(now)
    schedule.took_break(now + timedelta(minutes=50))
    assert schedule.due(now + timedelta(minutes=61), []) == []
    assert len(schedule.due(now + timedelta(minutes=111), [])) == 1
