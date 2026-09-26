"""A short briefing before a meeting: what happened last time and what you owe people.

Built only from what Buddy already has: minutes of earlier meetings with a similar title
(recurring meetings keep their title), and facts you asked him to remember that mention
the meeting's words. If nothing related exists there's no briefing and no Claude call."""

import anthropic

from .agent import MODEL, api_key

SYSTEM = """You brief someone just before a meeting, like a helpful colleague leaning over: \
one or two short sentences they can read or hear in five seconds. Mention only what's \
useful going in: open action items (especially theirs, owner "You"), decisions to \
remember, unresolved questions. Say when something is from a previous meeting. No \
greeting, no preamble, no lists, no markdown. If nothing in the notes is relevant to this \
meeting, reply with exactly: NONE"""


def gather(event, memory, limit=2):
    """Related notes for event, or "" if there's nothing."""
    minutes = [m for m in memory.search_minutes(event.title, limit=limit + 2)
               if _related(event.title, m.title)][:limit]
    facts = memory.recall(event.title, limit=3)
    parts = [f"Minutes of “{m.title}” on {m.held_at.astimezone().strftime('%a %d %b')}:\n{m.body[:2500]}"
             for m in minutes]
    if facts:
        parts.append("Things the user asked you to remember:\n" + "\n".join(f"- {f.text}" for f in facts))
    return "\n\n".join(parts)


COMMON = {"meeting", "meet", "sync", "call", "with", "weekly", "daily", "monthly", "team",
          "review", "update", "discussion", "catch", "about", "session", "chat", "check"}


def _words(title):
    return {w for w in title.lower().replace("-", " ").replace(":", " ").split()
            if len(w) > 2 and w not in COMMON}


def _related(a, b):
    """Titles share a meaningful word ("Uday project" vs "Uday sync"; "Weekly sync" alone
    doesn't relate to "Weekly review")."""
    return bool(_words(a) & _words(b))


def write_brief(event, notes, client=None):
    """One or two sentences, or None if the notes aren't relevant."""
    client = client or anthropic.Anthropic(api_key=api_key() or None)
    starts = event.start.astimezone().strftime("%H:%M")
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM,
        messages=[{"role": "user", "content":
                   f"Upcoming meeting: {event.title} at {starts}.\n\n<notes>\n{notes}\n</notes>"}],
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        return None
    text = " ".join(b.text for b in response.content if b.type == "text").strip()
    return None if not text or text.upper().startswith("NONE") else text
