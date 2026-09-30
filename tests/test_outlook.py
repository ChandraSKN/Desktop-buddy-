"""Outlook scheduling: validation, Graph boundary, and click-only confirmation."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QWidget

from deskbuddy.services.agent_tools import ToolBox
from deskbuddy.services.outlook import OutlookClient, OutlookError, make_draft
from deskbuddy.ui.outlook import OutlookScheduler

NOW = datetime(2099, 1, 1, tzinfo=UTC)


def draft(**kw):
    args = dict(subject="Review", start="2099-01-02T15:00:00+05:30", end="2099-01-02T15:30:00+05:30",
                attendees="ravi@example.com", now=NOW)
    args.update(kw)
    return make_draft(**args)


def test_payload_converts_to_utc_and_preserves_review():
    meeting = draft(teams=True, body="Discuss exports")
    payload = meeting.payload()
    assert payload["start"] == {"dateTime": "2099-01-02T09:30:00", "timeZone": "UTC"}
    assert payload["end"]["dateTime"] == "2099-01-02T10:00:00"
    assert payload["attendees"][0]["emailAddress"]["address"] == "ravi@example.com"
    assert payload["onlineMeetingProvider"] == "teamsForBusiness"
    assert payload["transactionId"] == meeting.payload()["transactionId"]
    assert "Discuss exports" in meeting.summary()


@pytest.mark.parametrize("changes", [
    {"start": "2099-01-02T15:00"}, {"end": "2099-01-02T15:00:00+05:30"},
    {"start": "2020-01-01T00:00:00Z"}, {"start": "tomorrow"}, {"subject": " "},
    {"attendees": "Ravi"}, {"attendees": "ravi@example.com\r\nBcc:x@example.com"},
])
def test_invalid_drafts_never_reach_outlook(changes):
    with pytest.raises(ValueError):
        draft(**changes)


def test_tool_can_only_propose():
    proposed = []
    box = ToolBox(None, lambda: [], now=lambda: NOW, outlook=SimpleNamespace(propose=proposed.append))
    result = box.run("draft_outlook_meeting", {"subject": "Review", "start": "2099-01-02T15:00+05:30",
                                             "end": "2099-01-02T15:30+05:30"})
    assert not result.is_error and "NOT scheduled" in result.content
    assert len(proposed) == 1
    assert box.run("create_outlook_meeting", {}).is_error


def test_create_uses_delegated_endpoint_and_stable_retry_id(monkeypatch, tmp_path):
    client = OutlookClient(tmp_path)
    monkeypatch.setattr(client, "_token", lambda: "fake-token")
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=201, json=lambda: {"id": "event-1"})

    monkeypatch.setattr("requests.post", post)
    meeting = draft()
    assert client.create(meeting)["id"] == "event-1"
    client.create(meeting)
    assert calls[0][0] == "https://graph.microsoft.com/v1.0/me/events"
    assert calls[0][1]["json"]["transactionId"] == calls[1][1]["json"]["transactionId"]
    assert calls[0][1]["allow_redirects"] is False


def test_create_permission_failure_is_not_success(monkeypatch, tmp_path):
    client = OutlookClient(tmp_path)
    monkeypatch.setattr(client, "_token", lambda: "fake-token")
    monkeypatch.setattr("requests.post", lambda *a, **kw: SimpleNamespace(status_code=403))
    with pytest.raises(OutlookError, match="denied"):
        client.create(draft())


def test_connect_persists_only_selected_account_and_private_cache(monkeypatch, tmp_path):
    client = OutlookClient(tmp_path / "account")
    cache = SimpleNamespace(serialize=lambda: "fake-cache")
    app = SimpleNamespace(acquire_token_interactive=lambda **kw: {"access_token": "fake"},
                          get_accounts=lambda: [{"username": "me@example.com", "home_account_id": "account-1"}])
    monkeypatch.setattr(client, "_app", lambda *a, **kw: (app, cache))
    assert client.connect("12345678-1234-1234-1234-123456789abc", "common") == "me@example.com"
    assert (client.root / "tokens.json").stat().st_mode & 0o777 == 0o600
    assert json.loads((client.root / "account.json").read_text())["account_id"] == "account-1"
    client.disconnect()
    assert not client.account and not (client.root / "tokens.json").exists()


def test_review_requires_click_and_sends_once(qtbot):
    buddy = QWidget()
    qtbot.addWidget(buddy)
    notes, sent = [], []
    buddy.chat = SimpleNamespace(note=notes.append)
    buddy.say = lambda *a: None
    buddy.sync_calendar = lambda: None
    client = SimpleNamespace(account="me@example.com", create=lambda d: sent.append(d) or {"id": "event"})
    scheduler = OutlookScheduler(buddy, client)
    meeting = draft()
    scheduler.review(meeting)
    assert not sent
    button = next(b for b in scheduler.dialog.findChildren(QPushButton) if b.text() == "Create meeting")
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: not scheduler.busy and bool(notes))
    assert sent == [meeting] and not button.isEnabled()
    scheduler.dialog.close()


def test_cancel_never_creates(qtbot):
    buddy = QWidget()
    qtbot.addWidget(buddy)
    buddy.chat = SimpleNamespace(note=lambda text: None)
    sent = []
    scheduler = OutlookScheduler(buddy, SimpleNamespace(account="me@example.com", create=sent.append))
    scheduler.review(draft())
    scheduler.dialog.reject()
    assert not sent and not scheduler.busy
