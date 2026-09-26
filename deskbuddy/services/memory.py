"""Buddy's long-term memory: facts you asked him to remember and reminders he set for you.

A local SQLite file (~/.local/share/desktop-buddy/memory.db). Facts are searched with
SQLite's FTS5 full-text index (BM25 ranking), so "what did I promise Ravi?" finds
"Promised Ravi the Q3 report by Friday". Nothing leaves the machine except the few facts
the assistant pulls into a conversation."""

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_PATH = Path.home() / ".local" / "share" / "desktop-buddy" / "memory.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    text, content='memories', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TABLE IF NOT EXISTS minutes (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    held_at TEXT NOT NULL,         -- UTC, ISO 8601
    path TEXT NOT NULL,
    body TEXT NOT NULL             -- the minutes without the transcript
);
CREATE VIRTUAL TABLE IF NOT EXISTS minutes_fts USING fts5(
    title, body, content='minutes', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS minutes_ai AFTER INSERT ON minutes BEGIN
    INSERT INTO minutes_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due_at TEXT NOT NULL,          -- UTC, ISO 8601
    created_at TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass(frozen=True)
class Memory:
    id: int
    text: str
    created_at: datetime


@dataclass(frozen=True)
class MeetingMinutes:
    id: int
    title: str
    held_at: datetime
    path: str
    body: str


@dataclass(frozen=True)
class UserReminder:
    id: int
    text: str
    due_at: datetime


def _iso(dt):
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _dt(text):
    return datetime.fromisoformat(text)


def _fts_query(text):
    """Turn free text into an FTS5 OR-query of its words (quoted, so punctuation is safe)."""
    words = [w for w in re.findall(r"\w+", text.lower()) if len(w) > 2]
    return " OR ".join(f'"{w}"' for w in words[:12])


class MemoryStore:
    def __init__(self, path=DEFAULT_PATH):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        # the assistant runs on a worker thread; each call is short and serialized by SQLite
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        if path != ":memory:":
            Path(path).chmod(0o600)

    # ---- facts
    def remember(self, text, now=None):
        now = now or datetime.now(UTC)
        with self.db:
            cur = self.db.execute("INSERT INTO memories(text, created_at) VALUES (?, ?)",
                                  (text.strip(), _iso(now)))
        return cur.lastrowid

    def recall(self, query, limit=5):
        q = _fts_query(query)
        if not q:
            return []
        rows = self.db.execute(
            "SELECT m.id, m.text, m.created_at FROM memories_fts f JOIN memories m ON m.id = f.rowid "
            "WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?", (q, limit)).fetchall()
        return [Memory(i, t, _dt(c)) for i, t, c in rows]

    def recent(self, limit=5):
        rows = self.db.execute("SELECT id, text, created_at FROM memories ORDER BY id DESC LIMIT ?",
                               (limit,)).fetchall()
        return [Memory(i, t, _dt(c)) for i, t, c in rows]

    def forget(self, memory_id):
        with self.db:
            return self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount > 0

    # ---- minutes of meetings
    def add_minutes(self, title, held_at, path, body):
        with self.db:
            return self.db.execute("INSERT INTO minutes(title, held_at, path, body) VALUES (?, ?, ?, ?)",
                                   (title, _iso(held_at), str(path), body)).lastrowid

    def search_minutes(self, query, limit=3):
        q = _fts_query(query)
        if q:
            rows = self.db.execute(
                "SELECT m.id, m.title, m.held_at, m.path, m.body FROM minutes_fts f "
                "JOIN minutes m ON m.id = f.rowid WHERE minutes_fts MATCH ? "
                "ORDER BY bm25(minutes_fts) LIMIT ?", (q, limit)).fetchall()
        else:
            rows = []
        if not rows:            # "what happened in my last meeting?" -> the most recent ones
            rows = self.db.execute("SELECT id, title, held_at, path, body FROM minutes "
                                   "ORDER BY held_at DESC LIMIT ?", (limit,)).fetchall()
        return [MeetingMinutes(i, t, _dt(h), p, b) for i, t, h, p, b in rows]

    # ---- reminders
    def add_reminder(self, text, due_at, now=None):
        now = now or datetime.now(UTC)
        with self.db:
            cur = self.db.execute("INSERT INTO reminders(text, due_at, created_at) VALUES (?, ?, ?)",
                                  (text.strip(), _iso(due_at), _iso(now)))
        return cur.lastrowid

    def pending_reminders(self):
        rows = self.db.execute("SELECT id, text, due_at FROM reminders WHERE delivered = 0 "
                               "ORDER BY due_at").fetchall()
        return [UserReminder(i, t, _dt(d)) for i, t, d in rows]

    def cancel_reminder(self, reminder_id):
        with self.db:
            return self.db.execute("DELETE FROM reminders WHERE id = ? AND delivered = 0",
                                   (reminder_id,)).rowcount > 0

    def take_due(self, now):
        """Reminders that are due, marked delivered so each fires once."""
        due = [r for r in self.pending_reminders() if r.due_at <= now]
        if due:
            with self.db:
                self.db.executemany("UPDATE reminders SET delivered = 1 WHERE id = ?",
                                    [(r.id,) for r in due])
        return due
