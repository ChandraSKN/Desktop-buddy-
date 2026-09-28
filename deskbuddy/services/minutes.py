"""Minutes of meeting: Claude turns a transcript into structured minutes, saved as Markdown.

Structured output (a pydantic schema) means the minutes always have the same sections,
and the Markdown is rendered here rather than trusting free-form model formatting."""

import json
from datetime import datetime
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from .agent import MODEL, api_key
from .transcribe import as_text

MIN_WORDS = 25                 # less than this and there's nothing to write minutes about

SYSTEM = """You write minutes of meeting from a transcript.

Speaker labels: "You" is the person the minutes are for (recorded from their microphone). \
Other people are labelled with their name when Buddy could see it on screen (Teams \
highlights whoever is talking; the name was read from their video tile, so it can be \
slightly misspelt or occasionally wrong), or "Others" when it couldn't tell. Use those names \
for owners and in the summary; otherwise use names only when the transcript makes clear \
who someone is (e.g. "Ravi, can you…") and say "You" or "the team".

The speech-to-text is imperfect and the meeting may mix English with Telugu or Hindi. \
Write the minutes in clear English. Translate non-English parts; silently fix obvious \
transcription errors when the meaning is clear; never invent decisions, owners or dates \
that weren't said. If something is ambiguous, put it under open questions.

Be concise: a summary of two to four sentences, and short bullet-style items."""


class ActionItem(BaseModel):
    owner: str = Field(description='Who will do it: a name, "You", or "Team" if unclear')
    task: str
    due: str = Field(description='When, as said in the meeting (e.g. "Friday"), or "" if not said')


class Minutes(BaseModel):
    summary: str
    action_items: list[ActionItem]
    decisions: list[str]
    discussion: list[str] = Field(description="Main points discussed, one line each")
    open_questions: list[str]


def write_minutes(segments, title, preferences=(), client=None):
    """Ask Claude for structured minutes. preferences: saved facts about how the user
    likes their minutes (from Buddy's memory)."""
    client = client or anthropic.Anthropic(api_key=api_key() or None)
    prefs = ("\n\nThe user's saved preferences:\n" + "\n".join(f"- {p}" for p in preferences)
             if preferences else "")
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM + prefs,
        messages=[{"role": "user", "content":
                   f"Meeting: {title}\n\n<transcript>\n{as_text(segments)}\n</transcript>"}],
        output_format=Minutes,
        thinking={"type": "adaptive"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError("Claude couldn't write minutes for this meeting.")
    return response.parsed_output


def word_count(segments):
    return sum(len(s["text"].split()) for s in segments)


def _local(iso):
    return datetime.fromisoformat(iso).astimezone()


def render(minutes, meta, segments):
    start, end = _local(meta["recorded_from"]), _local(meta["recorded_to"])
    when = f"{start.strftime('%a %d %b %Y, %H:%M')}–{end.strftime('%H:%M')}"
    if "start" in meta:
        when += f" (scheduled {_local(meta['start']).strftime('%H:%M')}–{_local(meta['end']).strftime('%H:%M')})"
    lines = [f"# Minutes: {meta['title']}", "", f"**When:** {when}  ", "",
             "## Summary", "", minutes.summary, ""]

    def section(heading, items):
        if items:
            lines.extend([f"## {heading}", "", *items, ""])

    section("Action items", [f"- [ ] **{a.owner}**: {a.task}" + (f" _(due {a.due})_" if a.due else "")
                             for a in minutes.action_items])
    section("Decisions", [f"- {d}" for d in minutes.decisions])
    section("Discussion", [f"- {d}" for d in minutes.discussion])
    section("Open questions", [f"- {q}" for q in minutes.open_questions])
    lines += ["<details><summary>Transcript (automatic, may contain errors)</summary>", "",
              "```", as_text(segments), "```", "", "</details>", ""]
    return "\n".join(lines)


def save(markdown, folder):
    """minutes.md next to the meeting's recording. (minutes.json, the "done" marker, is
    written by the caller once the recording files are finished too.)"""
    path = Path(folder) / "minutes.md"
    path.write_text(markdown)
    return path


def mark_done(folder, path):
    (Path(folder) / "minutes.json").write_text(json.dumps({"path": str(path)}))
