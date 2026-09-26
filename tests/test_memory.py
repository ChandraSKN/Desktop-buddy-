from datetime import UTC, datetime, timedelta

import pytest

from deskbuddy.services.memory import MemoryStore

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)


@pytest.fixture
def store():
    return MemoryStore(":memory:")


def test_recall_finds_facts_by_keyword_and_word_form(store):
    store.remember("Promised Ravi the Q3 report by Friday", NOW)
    store.remember("Prefers tea over coffee", NOW)
    found = store.recall("what did I promise ravi?")
    assert [m.text for m in found] == ["Promised Ravi the Q3 report by Friday"]
    assert store.recall("reports")[0].text.startswith("Promised")      # porter stemming


def test_recall_is_safe_with_punctuation_and_empty_queries(store):
    store.remember("Uses VS Code", NOW)
    assert store.recall('"; DROP TABLE memories; --') == []
    assert store.recall("?!") == []


def test_forget_removes_from_search(store):
    mid = store.remember("Allergic to peanuts", NOW)
    assert store.forget(mid)
    assert store.recall("peanuts") == []
    assert not store.forget(mid)


def test_reminders_fire_once_when_due(store):
    rid = store.add_reminder("Email Ravi", NOW + timedelta(minutes=30), NOW)
    assert store.take_due(NOW + timedelta(minutes=29)) == []
    due = store.take_due(NOW + timedelta(minutes=30))
    assert [r.id for r in due] == [rid]
    assert store.take_due(NOW + timedelta(hours=2)) == []
    assert store.pending_reminders() == []


def test_cancel_only_pending_reminders(store):
    rid = store.add_reminder("Stand up", NOW + timedelta(minutes=5), NOW)
    assert store.cancel_reminder(rid)
    assert not store.cancel_reminder(rid)


def test_database_file_is_private(tmp_path):
    path = tmp_path / "sub" / "memory.db"
    MemoryStore(path).remember("x")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
