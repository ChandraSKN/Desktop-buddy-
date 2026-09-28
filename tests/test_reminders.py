import unittest
from datetime import UTC, datetime, timedelta

from deskbuddy.services.reminders import (
    Event,
    ReminderSchedule,
    countdown,
    find_join_url,
    outlook_web_url,
    parse_events,
    spoken_reminder,
    upcoming,
    validate_url,
    voice_stage,
)

UTC = UTC
NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)

def ev(key, start, title, url=''):
    return Event(key, start, title, start+timedelta(minutes=30), url)


class RemindersTest(unittest.TestCase):
    def test_hourly_and_resume_does_not_flood(self):
        schedule = ReminderSchedule(NOW)
        self.assertEqual(schedule.due(NOW+timedelta(minutes=59), []), [])
        self.assertEqual(len(schedule.due(NOW+timedelta(hours=3), [])), 1)
        self.assertEqual(schedule.due(NOW+timedelta(hours=3, seconds=1), []), [])

    def test_meeting_lead_time_and_duplicate_suppression(self):
        schedule = ReminderSchedule(NOW)
        event = ev('one', NOW+timedelta(minutes=16), 'Review')
        self.assertEqual(schedule.due(NOW,[event]), [])
        self.assertEqual(len(schedule.due(NOW+timedelta(minutes=1),[event])),1)
        self.assertEqual(schedule.due(NOW+timedelta(minutes=2),[event]),[])
        # a meeting that has just started still reminds (you may not have joined yet)
        self.assertEqual([r.kind for r in schedule.due(NOW,[ev('old',NOW-timedelta(seconds=1),'Old')])],
                         ['meeting-now'])

    def test_recurrence_exclusion_cancelled_all_day_and_timezone(self):
        data = b'''BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:recurring\r
DTSTART;TZID=Asia/Kolkata:20260925T153000\r
DTEND;TZID=Asia/Kolkata:20260925T160000\r
RRULE:FREQ=DAILY;COUNT=4\r
EXDATE;TZID=Asia/Kolkata:20260927T153000\r
SUMMARY:Daily\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:cancel\r
DTSTART:20260926T100500Z\r
STATUS:CANCELLED\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:holiday\r
DTSTART;VALUE=DATE:20260926\r
END:VEVENT\r
END:VCALENDAR\r
'''
        events = parse_events(data,NOW)
        self.assertTrue(events)
        self.assertTrue(all(e[2]=='Daily' for e in events))
        self.assertTrue(any(e[1]==NOW for e in events))
        self.assertFalse(any(e[1].day==27 for e in events))

    def test_url(self):
        self.assertEqual(validate_url('webcal://example.com/calendar.ics'), 'https://example.com/calendar.ics')
        for bad in ('http://example.com','file:///tmp/calendar','https://user:pass@example.com'):
            with self.assertRaises(ValueError):
                validate_url(bad)

    def test_repeats_every_two_minutes_until_acknowledged(self):
        schedule = ReminderSchedule(NOW)
        event = ev('m', NOW+timedelta(minutes=10), 'Standup')
        kinds = []
        for second in range(0, 16*60, 10):          # simulate the 1-second checker, coarsely
            kinds += [r.kind for r in schedule.due(NOW+timedelta(seconds=second), [event])]
        # 10, 8, 6, 4, 2 min before; at the start; then 2, 4 min late (stops 10 min after start
        # only if still unacknowledged; here the loop ends at +16 min = 6 min late)
        self.assertEqual(kinds.count('meeting'), 5)
        self.assertEqual(kinds.count('meeting-now'), 1)
        self.assertGreaterEqual(kinds.count('meeting-late'), 2)

    def test_acknowledge_stops_reminders(self):
        schedule = ReminderSchedule(NOW)
        event = ev('m', NOW+timedelta(minutes=10), 'Standup')
        self.assertEqual(len(schedule.due(NOW, [event])), 1)
        schedule.acknowledge('m', NOW+timedelta(minutes=1))
        for minute in range(1, 25):
            self.assertEqual(schedule.due(NOW+timedelta(minutes=minute), [event]), [])

    def test_stops_after_grace_or_end(self):
        schedule = ReminderSchedule(NOW)
        long_ago = Event('x', NOW-timedelta(minutes=11), 'Old', NOW+timedelta(minutes=30), '')
        ended = Event('y', NOW-timedelta(minutes=3), 'Short', NOW-timedelta(seconds=1), '')
        self.assertEqual(schedule.due(NOW, [long_ago, ended]), [])

    def test_meeting_first_seen_at_start_reminds_once(self):
        schedule = ReminderSchedule(NOW)
        event = ev('late', NOW+timedelta(seconds=30), 'Sync')
        self.assertEqual([r.kind for r in schedule.due(NOW, [event])], ['meeting-now'])
        self.assertEqual(schedule.due(NOW+timedelta(seconds=10), [event]), [])

    def test_meeting_fifteen_minutes_away_reminds_now(self):
        schedule = ReminderSchedule(NOW)
        event = ev('soon', NOW+timedelta(minutes=15), 'Test 1')
        reminders = schedule.due(NOW, [event])
        self.assertEqual([r.text for r in reminders], ['Test 1 — starts in 15 min.'])

    def test_added_meetings_get_a_heads_up(self):
        schedule = ReminderSchedule(NOW)
        old = ev('old', NOW+timedelta(minutes=40), 'Old')
        self.assertEqual(schedule.added([old], NOW), [])          # first sync: nothing is "new"
        new = ev('new', NOW+timedelta(minutes=30), 'New')
        close = ev('close', NOW+timedelta(minutes=5), 'Close')      # gets a real reminder instead
        far = ev('far', NOW+timedelta(hours=3), 'Far')
        self.assertEqual(schedule.added([old, new, close, far], NOW), [new])
        self.assertEqual(schedule.added([old, new, close, far], NOW+timedelta(minutes=2)), [])

    def test_countdown(self):
        event = ev('c', NOW+timedelta(minutes=14, seconds=32), 'X')
        self.assertEqual(countdown(event, NOW), '14:32')
        self.assertEqual(countdown(event, NOW+timedelta(minutes=14, seconds=27)), '0:05')
        self.assertEqual(countdown(event, NOW+timedelta(minutes=17, seconds=44)), '-3:12')
        self.assertEqual(countdown(ev('h', NOW+timedelta(hours=1, seconds=9), 'X'), NOW), '1:00:09')

    def test_voice_stages_and_words(self):
        event = ev('v', NOW+timedelta(minutes=15), 'Test 1', 'https://meet.google.com/abc-defg-hij')
        self.assertEqual(voice_stage(event, NOW), 'early')
        self.assertEqual(voice_stage(event, NOW+timedelta(minutes=10)), 'soon')
        self.assertEqual(voice_stage(event, NOW+timedelta(minutes=15)), 'now')
        self.assertEqual(voice_stage(event, NOW+timedelta(minutes=18)), 'now')
        self.assertEqual(spoken_reminder(event, NOW), 'Heads up! Your meeting, Test 1, starts in 15 minutes.')
        self.assertEqual(spoken_reminder(event, NOW+timedelta(minutes=13)),
                         'Test 1 starts in 2 minutes. Time to get ready.')
        self.assertEqual(spoken_reminder(event, NOW+timedelta(minutes=15)),
                         'Test 1 is starting now. Press Join on my card.')
        self.assertIn('Open it from Outlook', spoken_reminder(ev('n', NOW, 'No link'), NOW))

    def test_outlook_web_link(self):
        self.assertIn('outlook.office.com', outlook_web_url('https://outlook.office365.com/owa/calendar/x/calendar.ics'))
        self.assertIn('outlook.live.com', outlook_web_url('https://outlook.live.com/owa/calendar/x/calendar.ics'))

    def test_join_links(self):
        teams = {'DESCRIPTION': 'Join: <https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc/0?context=x> more'}
        self.assertTrue(find_join_url(teams).startswith('https://teams.microsoft.com/l/meetup-join/'))
        self.assertFalse(find_join_url(teams).endswith('>'))
        meet = {'LOCATION': 'https://meet.google.com/abc-defg-hij', 'DESCRIPTION': 'https://aka.ms/JoinTeamsMeeting'}
        self.assertEqual(find_join_url(meet), 'https://meet.google.com/abc-defg-hij')
        self.assertEqual(find_join_url({'LOCATION': 'Room 4'}), '')

    def test_upcoming_skips_finished(self):
        done = Event('d', NOW-timedelta(hours=1), 'Done', NOW-timedelta(minutes=1), '')
        running = Event('r', NOW-timedelta(minutes=5), 'Running', NOW+timedelta(minutes=25), '')
        later = ev('l', NOW+timedelta(hours=2), 'Later')
        self.assertEqual([e.title for e in upcoming([done, running, later], NOW)], ['Running', 'Later'])


if __name__ == "__main__":
    unittest.main()
