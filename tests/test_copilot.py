"""Phase 4: Claude Code hooks, the command socket, and meeting briefings."""

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

import pytest

from deskbuddy import cli, ipc
from deskbuddy.services import briefing
from deskbuddy.services.claude_hooks import describe, handle, run_hook
from deskbuddy.services.memory import MemoryStore
from deskbuddy.services.reminders import Event

NOW = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)


# ---- Claude Code hooks
def test_long_task_tells_buddy_short_one_does_not(tmp_path):
    ev = {"session_id": "s1", "cwd": "/home/me/desktop-buddy"}
    assert handle({**ev, "hook_event_name": "UserPromptSubmit"}, tmp_path, now=1000) is None
    assert handle({**ev, "hook_event_name": "Stop"}, tmp_path, now=1030) is None           # 30 s
    handle({**ev, "hook_event_name": "UserPromptSubmit"}, tmp_path, now=2000)
    done = handle({**ev, "hook_event_name": "Stop"}, tmp_path, now=2240)                    # 4 min
    assert done == {"cmd": "claude", "kind": "done", "project": "desktop-buddy", "seconds": 240}
    assert describe(done) == ("✅ Claude Code finished", "desktop-buddy: done after 4 min")


def test_sessions_are_timed_separately_and_unknown_starts_are_ignored(tmp_path):
    handle({"session_id": "a", "hook_event_name": "UserPromptSubmit"}, tmp_path, now=0)
    assert handle({"session_id": "b", "hook_event_name": "Stop"}, tmp_path, now=500) is None
    assert handle({"session_id": "../../etc", "hook_event_name": "UserPromptSubmit"}, tmp_path, now=0) is None
    assert all(p.parent == tmp_path for p in tmp_path.iterdir())            # no path escape


def test_permission_prompts_always_notify(tmp_path):
    msg = handle({"hook_event_name": "Notification", "cwd": "/x/api",
                  "message": "Claude needs your permission to use Bash"}, tmp_path)
    assert msg["kind"] == "attention" and describe(msg)[1] == "api: Claude needs your permission to use Bash"


def test_a_broken_hook_event_never_raises():
    sent = []
    run_hook("not json", sent.append)
    run_hook('["a list"]', sent.append)
    assert sent == []


def test_buddy_command_when_buddy_is_not_running(capsys):
    assert cli.main(["say", "hello"]) == cli.NOT_RUNNING
    assert "isn't running" in capsys.readouterr().err


def test_claude_hook_command_never_fails(monkeypatch):
    monkeypatch.setattr("sys.stdin", NS(read=lambda: "{broken"))
    assert cli.main(["claude-hook"]) == 0


# ---- command socket, through the real window
@pytest.fixture
def buddy(qtbot, monkeypatch):
    from deskbuddy.ui.buddy_window import Buddy
    monkeypatch.setattr(Buddy, "sync_calendar", lambda self: None)
    b = Buddy()
    qtbot.addWidget(b)
    b.calendar_timer.stop()
    b.reminder_timer.stop()
    monkeypatch.setattr(b.notifier, "notify", lambda *a, **k: None)
    return b


def send_in_background(message, **kw):
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("r", ipc.send(message, timeout=3, **kw)))
    t.start()
    return t, result


def test_buddy_say_shows_the_message(buddy, qtbot):
    t, result = send_in_background({"cmd": "say", "text": "Build finished"})
    qtbot.waitUntil(lambda: "Build finished" in buddy.bubble.text, timeout=3000)
    t.join()
    assert result["r"] is True


def test_claude_finished_waves_and_says_so(buddy, qtbot):
    t, _ = send_in_background({"cmd": "claude", "kind": "done", "project": "api", "seconds": 300})
    qtbot.waitUntil(lambda: "Claude Code finished" in buddy.bubble.text, timeout=3000)
    t.join()
    assert buddy.wave_start is not None


def test_status_replies(buddy, qtbot):
    t, result = send_in_background({"cmd": "status"}, want_reply=True)
    qtbot.waitUntil(lambda: "r" in result, timeout=3000)
    t.join()
    assert result["r"]["state"] in ("idle", "walk") and "voice" in result["r"]


def test_junk_on_the_socket_is_ignored(buddy, qtbot):
    t, _ = send_in_background({"no": "cmd"})
    t.join()
    qtbot.wait(100)
    assert not buddy.bubble.isVisible() or "Hi!" in buddy.bubble.text


# ---- briefings
def meeting(title):
    return Event("k", NOW + timedelta(minutes=10), title, NOW + timedelta(minutes=40), "")


def test_brief_notes_come_from_related_minutes_and_memories():
    store = MemoryStore(":memory:")
    store.add_minutes("Uday co-axial project", NOW - timedelta(days=7), "/a.md",
                      "## Action items\n- [ ] **You**: send cable specs to Uday")
    store.add_minutes("Weekly sync", NOW - timedelta(days=1), "/b.md", "Nothing about cables.")
    store.remember("Uday prefers WhatsApp over email", NOW)
    notes = briefing.gather(meeting("Uday co-axial project"), store)
    assert "send cable specs to Uday" in notes and "WhatsApp" in notes
    assert "Nothing about cables" not in notes


def test_generic_titles_do_not_count_as_related():
    assert not briefing._related("Weekly sync", "Weekly review")
    assert briefing._related("Uday project sync", "Uday co-axial project")


def test_no_notes_means_no_brief_and_no_claude_call():
    assert briefing.gather(meeting("Brand new topic"), MemoryStore(":memory:")) == ""


def fake_client(text):
    return NS(beta=NS(messages=NS(create=lambda **kw: NS(
        stop_reason="end_turn", content=[NS(type="text", text=text)]))))


def test_write_brief_text_and_none():
    ev = meeting("Uday co-axial project")
    assert briefing.write_brief(ev, "notes", fake_client("You owe Uday the cable specs.")) == \
        "You owe Uday the cable specs."
    assert briefing.write_brief(ev, "notes", fake_client("NONE")) is None


def test_brief_appears_on_the_meeting_card(buddy, qtbot, monkeypatch):
    from deskbuddy.services import agent as agent_mod
    from deskbuddy.ui import buddy_window
    monkeypatch.setattr(agent_mod, "available", lambda: True)
    monkeypatch.setattr(buddy_window, "gather", lambda ev, mem: "notes")
    monkeypatch.setattr(buddy_window, "write_brief", lambda ev, notes: "Last time: send Uday the specs.")
    monkeypatch.setattr(buddy.speaker, "say", lambda text: True)
    buddy.brief = True
    ev = Event("uday", datetime.now(UTC) + timedelta(minutes=10), "Uday co-axial project",
               datetime.now(UTC) + timedelta(minutes=40), "")
    buddy.card.show_event(ev)
    buddy.start_brief(ev)
    qtbot.waitUntil(lambda: buddy.card.brief.isVisible(), timeout=3000)
    assert "send Uday the specs" in buddy.card.brief.text()
    buddy.start_brief(ev)                                      # once per meeting
    assert len(buddy.brief_workers) <= 1


def test_meeting_reminder_is_spoken_at_15_5_and_start_with_a_countdown(buddy, monkeypatch):
    from deskbuddy.services.reminders import Reminder
    from deskbuddy.ui import buddy_window
    said = []
    monkeypatch.setattr(buddy.speaker, "say", lambda text: said.append(text) or True)
    monkeypatch.setattr(buddy, "start_brief", lambda ev: None)
    buddy.speak = True
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=1)   # the card reads the real clock
    ev = Event("talk", start, "Test 1", start + timedelta(minutes=30), "")
    clock = [start - timedelta(minutes=15)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0]
    monkeypatch.setattr(buddy_window, "datetime", Clock)
    for minutes_before in (15, 13, 11, 5, 3, 0, -2):
        clock[0] = start - timedelta(minutes=minutes_before)
        buddy.deliver(Reminder("meeting", "", ev))
    assert said == ["Heads up! Your meeting, Test 1, starts in 15 minutes.",
                    "Test 1 starts in 5 minutes. Time to get ready.",
                    "Test 1 is starting now. Open it from Outlook."]
    assert buddy.card.countdown.text().startswith("⏱")
