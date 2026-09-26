"""The tools Buddy's assistant can use, and their handlers.

Deliberately small and local: read your meetings and their minutes, set/list/cancel
reminders, remember/recall/forget facts, and join a meeting. No email, no files, no shell.
Handlers validate their input themselves (tool inputs stream eagerly, so the API doesn't),
and anything the UI must do (opening a link) comes back as an action for the main thread.

Calendar text is untrusted (anyone can send you an invite), so join_meeting only opens a
join link that came from your own calendar feed, by meeting key, never an arbitrary URL."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .reminders import local_time, upcoming


def _tool(name, description, properties, required=()):
    return {
        "name": name,
        "description": description,
        "eager_input_streaming": True,
        "input_schema": {"type": "object", "properties": properties,
                         "required": list(required), "additionalProperties": False},
    }


TOOLS = [
    _tool("get_meetings",
          "List the user's meetings from their Outlook calendar that haven't ended, soonest first. "
          "Each has a key (for join_meeting / reminders), title, local start and end time, and "
          "whether it has a join link.",
          {"hours_ahead": {"type": "integer", "minimum": 1, "maximum": 48,
                           "description": "How far ahead to look (default 24)."}}),
    _tool("create_reminder",
          "Remind the user about something later: a desktop notification plus Buddy saying it. "
          "Give exactly one of: at (local date-time), in_minutes, or after_meeting (fires when "
          "that meeting ends).",
          {"text": {"type": "string", "description": "What to remind them of, phrased to them."},
           "at": {"type": "string", "description": "Local time, ISO 8601, e.g. 2026-09-27T15:30"},
           "in_minutes": {"type": "integer", "minimum": 1, "maximum": 60 * 24 * 14},
           "after_meeting": {"type": "string", "description": "A meeting key from get_meetings."}},
          ["text"]),
    _tool("list_reminders", "List reminders Buddy has set that haven't fired yet.", {}),
    _tool("cancel_reminder", "Cancel a pending reminder by id.",
          {"id": {"type": "integer"}}, ["id"]),
    _tool("remember",
          "Save a fact about the user or their work for future conversations (preferences, "
          "people, commitments). Only save what they'd want kept; one fact per call.",
          {"fact": {"type": "string"}}, ["fact"]),
    _tool("recall", "Search saved facts by keywords.",
          {"query": {"type": "string"}}, ["query"]),
    _tool("forget", "Delete a saved fact by id.", {"id": {"type": "integer"}}, ["id"]),
    _tool("search_minutes",
          "Search the minutes of meetings Buddy recorded (summary, action items, decisions). "
          "With no good keyword match it returns the most recent minutes.",
          {"query": {"type": "string", "description": "Meeting name, person or topic."}}, ["query"]),
    _tool("join_meeting", "Open a meeting's join link (Teams, Meet, Zoom…) in the browser.",
          {"key": {"type": "string", "description": "Meeting key from get_meetings."}}, ["key"]),
]

_TYPES = {"string": str, "integer": int}


def short_key(event):
    """Outlook meeting ids are ~150 characters; the model gets a stable 8-character one."""
    return hashlib.sha1(event.key.encode()).hexdigest()[:8]


class ToolInputError(ValueError):
    pass


def validate(name, args):
    """Check args against the tool's schema (required keys, no extras, types, ranges)."""
    schema = next((t["input_schema"] for t in TOOLS if t["name"] == name), None)
    if schema is None:
        raise ToolInputError(f"unknown tool {name!r}")
    if not isinstance(args, dict):
        raise ToolInputError("input must be an object")
    props = schema["properties"]
    for key in schema["required"]:
        if key not in args:
            raise ToolInputError(f"missing {key!r}")
    for key, value in args.items():
        if key not in props:
            raise ToolInputError(f"unexpected {key!r}")
        want = _TYPES[props[key]["type"]]
        if not isinstance(value, want) or isinstance(value, bool):
            raise ToolInputError(f"{key!r} must be a {props[key]['type']}")
        if want is str and not value.strip():
            raise ToolInputError(f"{key!r} is empty")
        lo, hi = props[key].get("minimum"), props[key].get("maximum")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise ToolInputError(f"{key!r} out of range")


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    open_url: str | None = None          # for the UI thread to open


@dataclass
class ToolBox:
    memory: object                        # MemoryStore
    get_events: object                    # () -> list[Event], the calendar as last synced
    now: object = field(default=lambda: datetime.now(UTC))

    def run(self, name, args):
        try:
            validate(name, args)
            return getattr(self, "_" + name)(**args)
        except ToolInputError as exc:
            return ToolResult(json.dumps({"INVALID_INPUT": str(exc), "input": args}), is_error=True)

    def _event(self, key):
        return next((e for e in self.get_events() if short_key(e) == key.strip()), None)

    def _get_meetings(self, hours_ahead=24):
        now = self.now()
        items = [e for e in upcoming(self.get_events(), now, limit=20)
                 if e.start <= now + timedelta(hours=hours_ahead)]
        return ToolResult(json.dumps([
            {"key": short_key(e), "title": e.title,
             "start": e.start.astimezone().strftime("%a %d %b %H:%M"), "end": local_time(e.end),
             "has_join_link": bool(e.url)} for e in items]) if items else "No meetings in that window.")

    def _create_reminder(self, text, at=None, in_minutes=None, after_meeting=None):
        given = [x for x in (at, in_minutes, after_meeting) if x is not None]
        if len(given) != 1:
            raise ToolInputError("give exactly one of at, in_minutes, after_meeting")
        now = self.now()
        if in_minutes is not None:
            due = now + timedelta(minutes=in_minutes)
        elif after_meeting is not None:
            event = self._event(after_meeting)
            if event is None:
                raise ToolInputError(f"no meeting with key {after_meeting!r}; call get_meetings")
            due = event.end
        else:
            try:
                due = datetime.fromisoformat(at)
            except ValueError:
                raise ToolInputError("at must be ISO 8601, e.g. 2026-09-27T15:30") from None
            if due.tzinfo is None:
                due = due.astimezone()            # a naive time is the user's local time
        if due <= now:
            raise ToolInputError("that time has already passed")
        rid = self.memory.add_reminder(text, due, now)
        return ToolResult(f"Reminder {rid} set for {due.astimezone().strftime('%a %d %b %H:%M')}.")

    def _list_reminders(self):
        items = self.memory.pending_reminders()
        return ToolResult(json.dumps([
            {"id": r.id, "text": r.text, "due": r.due_at.astimezone().strftime("%a %d %b %H:%M")}
            for r in items]) if items else "No pending reminders.")

    def _cancel_reminder(self, id):
        ok = self.memory.cancel_reminder(id)
        return ToolResult("Cancelled." if ok else f"No pending reminder {id}.", is_error=not ok)

    def _remember(self, fact):
        return ToolResult(f"Saved as memory {self.memory.remember(fact, self.now())}.")

    def _recall(self, query):
        found = self.memory.recall(query)
        return ToolResult(json.dumps([{"id": m.id, "fact": m.text, "saved": m.created_at.date().isoformat()}
                                      for m in found]) if found else "Nothing saved about that.")

    def _forget(self, id):
        ok = self.memory.forget(id)
        return ToolResult("Forgotten." if ok else f"No memory {id}.", is_error=not ok)

    def _search_minutes(self, query):
        found = self.memory.search_minutes(query)
        if not found:
            return ToolResult("No minutes recorded yet.")
        return ToolResult(json.dumps([
            {"title": m.title, "held": m.held_at.astimezone().strftime("%a %d %b %Y %H:%M"),
             "file": m.path, "minutes": m.body[:4000]} for m in found], ensure_ascii=False))

    def _join_meeting(self, key):
        event = self._event(key)
        if event is None:
            raise ToolInputError(f"no meeting with key {key!r}; call get_meetings")
        if not event.url:
            return ToolResult("That meeting has no join link in the invite.", is_error=True)
        return ToolResult(f"Opening the join link for {event.title}.", open_url=event.url)
