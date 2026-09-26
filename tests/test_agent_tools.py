import json
from datetime import UTC, datetime, timedelta

import pytest

from deskbuddy.services.agent_tools import TOOLS, ToolBox
from deskbuddy.services.memory import MemoryStore
from deskbuddy.services.reminders import Event

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
STANDUP = Event("standup", NOW + timedelta(hours=1), "Standup", NOW + timedelta(hours=1, minutes=30),
                "https://teams.microsoft.com/l/meetup-join/abc")
LUNCH = Event("lunch", NOW + timedelta(hours=3), "Lunch with Priya", NOW + timedelta(hours=4), "")


@pytest.fixture
def box():
    return ToolBox(MemoryStore(":memory:"), lambda: [STANDUP, LUNCH], now=lambda: NOW)


def test_every_tool_has_a_handler_and_a_strict_schema(box):
    for tool in TOOLS:
        assert hasattr(box, "_" + tool["name"])
        assert tool["input_schema"]["additionalProperties"] is False


def test_get_meetings_respects_the_window(box):
    items = json.loads(box.run("get_meetings", {"hours_ahead": 2}).content)
    assert [m["key"] for m in items] == ["standup"]
    assert items[0]["has_join_link"] is True


def test_reminder_after_a_meeting_fires_when_it_ends(box):
    result = box.run("create_reminder", {"text": "Email Ravi", "after_meeting": "standup"})
    assert not result.is_error
    assert box.memory.pending_reminders()[0].due_at == STANDUP.end


def test_reminder_at_a_naive_local_time(box):
    local = (NOW + timedelta(days=1)).astimezone().replace(tzinfo=None, second=0, microsecond=0)
    result = box.run("create_reminder", {"text": "Gym", "at": local.isoformat()})
    assert not result.is_error
    assert box.memory.pending_reminders()[0].due_at == local.astimezone()


@pytest.mark.parametrize("args", [
    {"text": "x"},                                              # no time at all
    {"text": "x", "in_minutes": 5, "at": "2026-09-27T10:00"},    # two times
    {"text": "x", "in_minutes": 0},                              # out of range
    {"text": "x", "in_minutes": "5"},                            # wrong type
    {"text": "x", "in_minutes": True},                           # bool is not an int
    {"text": "x", "at": "tomorrow"},                             # not ISO
    {"text": "x", "at": "2020-01-01T10:00"},                     # in the past
    {"text": "x", "after_meeting": "nope"},                      # unknown meeting
    {"text": "  ", "in_minutes": 5},                             # empty text
    {"text": "x", "in_minutes": 5, "extra": 1},                  # unexpected key
])
def test_bad_reminder_input_is_an_error_not_a_reminder(box, args):
    result = box.run("create_reminder", args)
    assert result.is_error and "INVALID_INPUT" in result.content
    assert box.memory.pending_reminders() == []


def test_remember_recall_forget(box):
    box.run("remember", {"fact": "Ravi owns the billing service"})
    found = json.loads(box.run("recall", {"query": "billing"}).content)
    assert found[0]["fact"] == "Ravi owns the billing service"
    assert not box.run("forget", {"id": found[0]["id"]}).is_error
    assert box.run("recall", {"query": "billing"}).content == "Nothing saved about that."


def test_join_meeting_only_opens_calendar_links(box):
    assert box.run("join_meeting", {"key": "standup"}).open_url == STANDUP.url
    assert box.run("join_meeting", {"key": "lunch"}).open_url is None       # no link
    bad = box.run("join_meeting", {"key": "https://evil.example"})
    assert bad.is_error and bad.open_url is None
