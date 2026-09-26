"""Outlook ICS reader and reminder scheduling; no mail or correction text is read."""
import re
import urllib.request
from collections import namedtuple
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from PyQt6.QtCore import QThread, pyqtSignal

LEAD = timedelta(minutes=10)       # first reminder this long before a meeting
REPEAT = timedelta(minutes=2)      # then remind again this often until Join/Dismiss
GRACE = timedelta(minutes=10)      # keep reminding this long after the start
NOW_WINDOW = timedelta(minutes=1)  # within this of the start counts as "starting now"

WELLNESS_TEXT = "Time for water! Stand up, stretch, and walk around for a couple of minutes."

Event = namedtuple("Event", "key start title end url")

# Online-meeting links, most specific first.
JOIN_PATTERNS = [
    r"https://teams\.microsoft\.com/l/meetup-join/[^\s<>\"']+",
    r"https://teams\.live\.com/meet/[^\s<>\"']+",
    r"https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}",
    r"https://[\w.-]*zoom\.us/[jw]/[^\s<>\"']+",
    r"https://[\w.-]*webex\.com/[^\s<>\"']+",
]


@dataclass
class Reminder:
    kind: str                 # "meeting", "meeting-now", "meeting-late" or "wellness"
    text: str                 # one-line summary for the speech bubble / notification
    event: Event | None = None


def validate_url(url):
    url = url.strip()
    if url.startswith('webcal://'):
        url = 'https://' + url[9:]
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Use the HTTPS ICS subscription link from Outlook.')
    return url


def find_join_url(event):
    fields = ("X-MICROSOFT-SKYPETEAMSMEETINGURL", "X-MICROSOFT-ONLINEMEETINGCONFLINK",
              "URL", "LOCATION", "DESCRIPTION")
    text = " ".join(str(event.get(f, "")) for f in fields)
    for pattern in JOIN_PATTERNS:
        match = re.search(pattern, text)
        if match:
            return match.group(0).rstrip(".,;)>]")
    return ""


def parse_events(data, now):
    import icalendar
    import recurring_ical_events
    calendar = icalendar.Calendar.from_ical(data)
    events = []
    for event in recurring_ical_events.of(calendar).between(now-timedelta(days=1), now+timedelta(days=2)):
        if str(event.get('STATUS', '')).upper() == 'CANCELLED':
            continue
        start = event.decoded('DTSTART')
        if not isinstance(start, datetime):
            continue  # all-day items have no meeting start time
        if start.tzinfo is None:
            start = start.astimezone()
        start = start.astimezone(timezone.utc)
        try:
            end = event.decoded('DTEND')
            end = (end if end.tzinfo else end.astimezone()).astimezone(timezone.utc)
        except (KeyError, AttributeError):
            end = start + timedelta(minutes=30)
        key = str(event.get('UID', '')) + '|' + start.isoformat()
        title = " ".join(str(event.get('SUMMARY', 'Meeting')).split()) or "Meeting"
        events.append(Event(key, start, title, end, find_join_url(event)))
    return sorted(events, key=lambda e: e.start)


class CalendarWorker(QThread):
    loaded = pyqtSignal(list)
    failed = pyqtSignal(str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            request = urllib.request.Request(validate_url(self.url), headers={'User-Agent':'DesktopBuddy/2'})
            with urllib.request.urlopen(request, timeout=20) as response:
                if urlparse(response.url).scheme != 'https':
                    raise ValueError('Calendar redirected to an insecure connection.')
                data = response.read(5_000_001)
            if len(data) > 5_000_000:
                raise ValueError('Calendar feed is too large.')
            self.loaded.emit(parse_events(data, datetime.now(timezone.utc)))
        except ImportError:
            self.failed.emit('Calendar dependencies missing. Run setup.sh, then restart Buddy.')
        except Exception:
            # Never show the subscription URL (it can contain a private token).
            self.failed.emit('Calendar sync failed. Check your connection and Outlook ICS link.')


def upcoming(events, now, limit=6):
    """Meetings that haven't ended yet, soonest first."""
    return [e for e in events if e.end > now][:limit]


def outlook_web_url(feed_url):
    """Outlook on the web's calendar, for meetings whose invite has no join link."""
    host = urlparse(feed_url or "").hostname or ""
    if host.endswith("live.com") or host.endswith("outlook.com"):
        return "https://outlook.live.com/calendar/0/view/day"
    return "https://outlook.office.com/calendar/view/day"


def meeting_text(event, now):
    until = event.start - now
    if until > NOW_WINDOW:
        return "meeting", f"starts in {max(1, round(until.total_seconds() / 60))} min"
    if until >= -NOW_WINDOW:
        return "meeting-now", "starting now"
    return "meeting-late", f"started {round(-until.total_seconds() / 60)} min ago"


class ReminderSchedule:
    """Wellness reminders every hour. Meeting reminders start LEAD before the meeting and
    repeat every REPEAT (plus once exactly at the start) until acknowledged via
    acknowledge(), or until GRACE after the start / the meeting's end."""

    def __init__(self, now):
        self.next_wellness = now + timedelta(hours=1)
        self.next_at = {}     # meeting key -> when to remind next
        self.acked = {}       # meeting key -> forget after this time

    def acknowledge(self, key, now):
        self.acked[key] = now + timedelta(days=1)
        self.next_at.pop(key, None)

    def due(self, now, events, wellness=True):
        reminders = []
        self.acked = {k: t for k, t in self.acked.items() if t > now}
        live = set()
        for event in events:
            until = event.start - now
            if event.key in self.acked or until > LEAD or until < -GRACE or now >= event.end:
                continue
            live.add(event.key)
            nxt = self.next_at.get(event.key)
            if nxt is not None and now < nxt:
                continue
            kind, when = meeting_text(event, now)
            reminders.append(Reminder(kind, f'{event.title[:120]} — {when}.', event))
            nxt = now + REPEAT
            if now + NOW_WINDOW < event.start < nxt:
                nxt = event.start          # make sure there's a "starting now" reminder
            self.next_at[event.key] = nxt
        self.next_at = {k: t for k, t in self.next_at.items() if k in live}
        if now >= self.next_wellness:
            self.next_wellness = now+timedelta(hours=1)
            if wellness:
                reminders.append(Reminder("wellness", WELLNESS_TEXT))
        return reminders
