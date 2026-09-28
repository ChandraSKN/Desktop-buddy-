"""Buddy's own window: the character, walking and docking, the chair, the right-click menu,
and delivering reminders."""

import math
import os
import random
import time
from collections import deque
from datetime import UTC, datetime, timedelta

from PyQt6.QtCore import QPoint, QSettings, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QBitmap, QDesktopServices, QGuiApplication, QImage, QPainter, QRegion
from PyQt6.QtWidgets import QApplication, QInputDialog, QLineEdit, QMenu, QMessageBox, QWidget

from ..character import chair, model3d
from ..config import (
    AWAY_AFTER,
    CHAIR_PAD,
    DOCK_SPEED,
    FPS,
    HOVER_HOLD,
    MEMORY_DB,
    SIT_AFTER,
    STEP_RATE,
    VOICE,
    WALK_SPEED,
    WIN_H,
    WIN_W,
)
from ..services.agent import Agent
from ..services.agent_tools import ToolBox
from ..services.briefing import gather, write_brief
from ..services.claude_hooks import describe as describe_claude
from ..services.code_tasks import summary as code_summary
from ..services.idle import IdleMonitor
from ..services.launcher import Launcher
from ..services.listener import CONVERSATION, is_goodbye
from ..services.memory import MemoryStore
from ..services.notifier import Notifier
from ..services.reminders import (
    WELLNESS_TEXT,
    CalendarWorker,
    Event,
    Reminder,
    ReminderSchedule,
    local_time,
    meeting_text,
    outlook_web_url,
    spoken_reminder,
    upcoming,
    validate_url,
    voice_stage,
)
from .assistant_panel import AssistantPanel
from .bubble import Bubble
from .code_task import CodeCard, CodeRunner
from .command_server import CommandServer
from .corrector_panel import CorrectorPanel
from .meeting_card import MeetingCard
from .minutes_bar import MinutesBar
from .timers import after
from .voice import ListenerThread, Speaker
from .whatsapp import DraftCard, LinkDialog, WhatsAppLink


class Buddy(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.resize(WIN_W, WIN_H)

        self.settings = QSettings("DesktopBuddy", "DesktopBuddy")
        self.docked = self.settings.value("docked", True, type=bool)
        self.wellness = self.settings.value("wellness", True, type=bool)

        self.update_geometry(initial=True)
        self.vy = 0.0
        self.facing = -1         # 1 = right, -1 = left (docked on the right, he faces the screen)
        self.yaw = -0.22         # 3D model's rotation
        self.phase = 0.0         # walk cycle
        self.breath = 0.0
        self.state = "idle"
        self.state_until = time.monotonic() + 1.5
        self.target_x = self.fx
        self.paused = False      # user pause from the menu
        self.hovered = False
        self.last_pointer = 0.0  # when the pointer last entered or moved over him
        self.busy = False        # correction panel open
        self._press = None
        self._drag_offset = None
        self._last = time.monotonic()
        self._frame = None
        self._mask_tick = 0
        self.wave_start = None
        self.wave_until = 0.0
        self.last_click = time.monotonic()
        self.pad = 0             # window grows to the left while the chair is out
        self.chair = None        # ChairScene while pulling up / sitting / standing

        self.memory = MemoryStore(MEMORY_DB)
        self.launcher = Launcher()
        self.whatsapp = WhatsAppLink(self)
        self.whatsapp.draft_ready.connect(self.on_draft)
        self.whatsapp.sent.connect(self.on_sent)
        self.whatsapp.state_changed.connect(lambda state: print(f"whatsapp: {state}", flush=True))
        self.draft_card = DraftCard(self)
        self.draft_by_voice = False
        self.link_dialog = None
        self.code = CodeRunner(self)
        self.code.proposed.connect(self.on_code_proposed)
        self.code.progress.connect(self.on_code_progress)
        self.code.finished.connect(self.on_code_finished)
        self.code_card = CodeCard(self)
        self.code_by_voice = False
        if self.whatsapp.linked:
            after(self, 4000, self.whatsapp.start)
        self.fixer = CorrectorPanel(self)
        self.chat = AssistantPanel(self, self.make_agent)
        self.minutes = MinutesBar(self, self.memory)
        self.listen = self.settings.value("voice_listen", True, type=bool)
        self.speak = self.settings.value("voice_speak", True, type=bool)
        self.voice_state = "off"
        self.speaker = Speaker(self)
        self.listener = ListenerThread()
        self.listener.wake.connect(self.on_wake)
        self.listener.heard.connect(self.on_heard)
        self.listener.awaiting.connect(self.on_awaiting)
        self.speaker.finished.connect(self._reply_spoken)
        self.reply_pending = False           # a spoken answer is playing; a follow-up may come
        self.listener.status.connect(self._voice_status)
        self.brief = self.settings.value("meeting_brief", True, type=bool)
        self.briefed, self.brief_workers = set(), []
        self.commands = CommandServer(self)
        self.commands.command.connect(self.handle_command)
        if not self.commands.start():
            print("command socket unavailable: buddy/Claude Code messages won't arrive", flush=True)
        if self.listen and VOICE:
            after(self, 2500, self.listener.start)
        after(self, 3000, self.minutes.resume_pending)
        self.bubble = Bubble()
        self.card = MeetingCard(self)
        self.notifier = Notifier(self)
        self.notifier.action.connect(self.notification_clicked)
        self.idle = IdleMonitor(AWAY_AFTER, self)
        self.idle.returned.connect(self.welcome_back)

        self.events = []
        self.pending = deque()
        self.calendar_worker = None
        self.calendar_ok = None
        self.calendar_status = "Not connected"
        self.schedule = ReminderSchedule(datetime.now(UTC))
        self.spoken = {}                # meeting key -> voice stages already said aloud
        self.reminder_timer = QTimer(self, timeout=self.check_reminders)
        self.reminder_timer.start(1000)
        self.calendar_timer = QTimer(self, timeout=self.sync_calendar)
        self.calendar_timer.start(2 * 60 * 1000)
        after(self, 1500, self.sync_calendar)
        QGuiApplication.primaryScreen().availableGeometryChanged.connect(lambda *_: self.update_geometry())
        QGuiApplication.instance().screenAdded.connect(lambda *_: self.update_geometry())
        QGuiApplication.instance().screenRemoved.connect(lambda *_: self.update_geometry())

        self.timer = QTimer(self, timeout=self.tick)
        self.timer.start(int(1000 / FPS))
        self.move(int(self.fx), int(self.fy))
        after(self, 800, lambda: self.say("Hi! Click me and ask me anything ✨", 4500))

    # ---- placement ------------------------------------------------------------
    def update_geometry(self, initial=False):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.floor_y = screen.bottom() - self.height() + 4      # shoes rest on the taskbar/dock
        self.min_x = screen.left() + 8
        self.max_x = max(self.min_x, screen.right() - WIN_W + 20)
        if initial:
            self.fx = float(self.max_x)
        else:
            self.fx = float(self.max_x if self.docked else max(self.min_x, min(self.max_x, self.fx)))
            self.target_x = max(self.min_x, min(self.max_x, self.target_x))
        self.fy = float(self.floor_y)

    def toggle_dock(self):
        self.docked = not self.docked
        self.settings.setValue("docked", self.docked)
        self.update_geometry()
        self.state = "idle"
        self.say("I'll stay on your right." if self.docked else "Let's take a walk!")

    def toggle_wellness(self):
        self.wellness = not self.wellness
        self.settings.setValue("wellness", self.wellness)
        self.schedule.next_wellness = datetime.now(UTC) + timedelta(hours=1)

    # ---- calendar -------------------------------------------------------------
    def configure_outlook(self):
        url, ok = QInputDialog.getText(self, "Outlook meeting reminders",
            "Paste Outlook's published ICS subscription link.\n"
            "Outlook web: Settings → Calendar → Shared calendars → Publish.\n"
            "Anyone with this link can read the published calendar.\n"
            "Leave empty to disconnect. Stored locally on this computer.",
            QLineEdit.EchoMode.Password, self.settings.value("calendar_url", ""))
        if not ok:
            return
        try:
            url = validate_url(url) if url.strip() else ""
        except ValueError as exc:
            QMessageBox.warning(self, "Calendar link", str(exc))
            return
        self.settings.setValue("calendar_url", url)
        self.settings.sync()
        try:
            os.chmod(self.settings.fileName(), 0o600)
        except OSError:
            pass
        self.events = []
        self.calendar_ok = None
        self.calendar_status = "Not connected" if not url else "Connecting…"
        self.sync_calendar()

    def sync_calendar(self):
        url = self.settings.value("calendar_url", "")
        if not url or (self.calendar_worker and self.calendar_worker.isRunning()):
            return
        self.calendar_worker = CalendarWorker(url, self)
        self.calendar_worker.loaded.connect(lambda events, source=url: self.calendar_loaded(events, source))
        self.calendar_worker.failed.connect(lambda msg, source=url: self.calendar_failed(msg, source))
        self.calendar_worker.start()

    def calendar_loaded(self, events, source):
        if source != self.settings.value("calendar_url", ""):
            return
        first = self.calendar_ok is None
        self.events = events
        self.calendar_ok = True
        self.calendar_status = "Synced at " + datetime.now().strftime("%H:%M")
        if first:
            nxt = upcoming(events, datetime.now(UTC), 1)
            if nxt:
                self.say(f"Calendar connected. Next: {nxt[0].title[:40]} at {local_time(nxt[0].start)}", 6000)
        for ev in self.schedule.added(events, datetime.now(UTC)):
            _, when = meeting_text(ev, datetime.now(UTC))
            self.notifier.notify(f"📅 New meeting: {ev.title}", f"{when[0].upper()}{when[1:]} · "
                                 f"{local_time(ev.start)}–{local_time(ev.end)}", "meeting", ev.key)
            self.pending.append(f"📅 New meeting: {ev.title[:60]} at {local_time(ev.start)} ({when}).")
        self.check_reminders()

    def calendar_failed(self, message, source):
        if source != self.settings.value("calendar_url", ""):
            return
        # Keep the meetings we already have, so a brief network drop doesn't lose reminders.
        self.calendar_status = message + (" (showing last sync)" if self.events else "")
        if self.calendar_ok is not False:
            self.say(message, 8000)
        self.calendar_ok = False

    # ---- reminders ------------------------------------------------------------
    def check_reminders(self):
        self.minutes.tick()
        # don't listen while speaking (he'd hear himself) or while recording minutes
        self.listener.hold = self.speaker.speaking or self.minutes.recorder.recording
        here = not self.idle.tracker.away       # no break reminders for an empty desk
        for reminder in self.schedule.due(datetime.now(UTC), self.events,
                                          self.wellness and here):
            self.deliver(reminder)
        for reminder in self.memory.take_due(datetime.now(UTC)):
            self.deliver_own(reminder)
        if self.pending and not self.busy and not self.bubble.isVisible():
            self.say(self.pending.popleft(), 20000)
            self.wave(6)

    def deliver(self, reminder):
        """Desktop notification right away, plus Buddy's own card or speech bubble.
        Meeting reminders come back every 2 minutes until you press Join or Dismiss."""
        self.stand_up()
        if reminder.event is not None:
            ev = reminder.event
            _, when = meeting_text(ev, datetime.now(UTC))
            body = f"{when[0].upper()}{when[1:]} · {local_time(ev.start)}–{local_time(ev.end)}"
            if ev.url:
                body += f"\n{ev.url}"
                actions = [("join", "Join meeting"), ("dismiss", "Dismiss")]
            else:
                body += "\nNo meeting link in this invite."
                actions = [("outlook", "Open in Outlook"), ("dismiss", "Dismiss")]
            self.notifier.notify(f"📅 {ev.title}", body, "meeting", ev.key, actions)
            self.card.show_event(ev)
            self.speak_reminder(ev)
            self.start_brief(ev)
            self.wave(12)
        else:
            self.notifier.notify("💧 Time for a break", WELLNESS_TEXT, "wellness")
            self.pending.append(reminder.text)

    def speak_reminder(self, event):
        """Say a meeting reminder out loud at 15 min, 5 min and the start (not every repeat)."""
        now = datetime.now(UTC)
        stage = voice_stage(event, now)
        said = self.spoken.setdefault(event.key, set())
        if stage in said or not self.speak or self.on_call():
            return
        said.add(stage)
        self.speaker.say(spoken_reminder(event, now))

    def deliver_own(self, reminder):
        """A reminder the assistant set for you ("remind me after the 3 pm meeting…")."""
        self.stand_up()
        self.notifier.notify("⏰ Reminder", reminder.text, "meeting", f"reminder-{reminder.id}",
                             [("dismiss", "Got it")])
        self.say(f"⏰ {reminder.text}", 30000)
        self.wave(8)

    def make_agent(self):
        return Agent(ToolBox(self.memory, lambda: self.events, launcher=self.launcher,
                             whatsapp=self.whatsapp, code=self.code))

    def outlook_url(self):
        return outlook_web_url(self.settings.value("calendar_url", ""))

    def notification_clicked(self, key, action):
        event = next((e for e in self.events + [self.card.event] if e and e.key == key), None)
        if action == "open-minutes":
            self.minutes.open_result()
        if action == "join" and event and event.url:
            self.join_meeting(event)
        elif action == "outlook":
            QDesktopServices.openUrl(QUrl(self.outlook_url()))
        if action in ("join", "outlook", "dismiss"):
            self.acknowledge(key)

    # ---- messages from other programs (`buddy` command, Claude Code hooks) ----------
    def handle_command(self, message, reply):
        cmd = message["cmd"]
        if cmd == "status":
            reply(self.status())
        elif cmd == "say" and str(message.get("text", "")).strip():
            text = str(message["text"])[:500]
            self.stand_up()
            self.say(text, max(4000, 60 * len(text)))
            self.wave(3)
            if message.get("speak") and self.speak:
                self.speaker.say(text)
        elif cmd == "open" and str(message.get("what", "")).strip():
            ok, text = self.launcher.open(str(message["what"])[:200])
            reply({"ok": ok, "message": text})
            if text:
                self.say(text if ok else text.replace("Opening", "I couldn't open"), 3000)
        elif cmd == "claude" and message.get("kind") in ("done", "attention"):
            title, body = describe_claude(message)
            self.notifier.notify(title, body, "wellness", f"claude-{message.get('project')}")
            self.stand_up()
            self.say(f"{title}\n{body}", 10000)
            self.wave(4)
            if self.speak and self.idle.idle_seconds >= 20 and not self.on_call():
                self.speaker.say("Claude Code is done." if message["kind"] == "done"
                                 else "Claude Code needs you.")

    def on_call(self):
        return self.voice_state == "paused: on a call" or self.minutes.recorder.recording

    def status(self):
        return {"state": self.state, "chair": self.chair and self.chair.phase, "voice": self.voice_state,
                "speaking": self.speaker.speaking, "recording_minutes": self.minutes.recorder.recording,
                "you_idle_seconds": round(self.idle.idle_seconds), "away": self.idle.tracker.away,
                "meetings_known": len(self.events)}

    # ---- briefing before meetings --------------------------------------------------
    def start_brief(self, event):
        if not self.brief or event.key in self.briefed or event.key == "preview":
            return
        self.briefed.add(event.key)
        worker = BriefWorker(event, self.memory)
        worker.ready.connect(self.brief_ready)
        worker.finished.connect(lambda w=worker: self.brief_workers.remove(w))
        self.brief_workers.append(worker)
        worker.start()

    def brief_ready(self, key, text):
        self.card.set_brief(key, text)
        if self.speak and not self.on_call():
            self.speaker.say(text)

    def toggle_brief(self):
        self.brief = not self.brief
        self.settings.setValue("meeting_brief", self.brief)

    # ---- voice ------------------------------------------------------------------
    def _voice_status(self, state):
        self.voice_state = state
        print(f"voice: {state}", flush=True)
        if state.startswith("error"):
            self.say("I can't hear you: " + state[7:], 6000)

    def on_wake(self):
        self.stand_up()
        self.reply_pending = False           # "Hey Buddy" already opens its own window
        self.speaker.stop()
        self.say("👂 Yes?", 8000)

    def on_heard(self, text, language="en"):
        self.stand_up()
        if is_goodbye(text) and not (self.draft_card.draft or self.code_card.proposal):
            # "thanks" ends the conversation; "no" to a card is an answer
            self.say("👍", 2000)
            return
        self.say(f"🎤 “{text}”", 20000)
        if not self.chat.ask(text, spoken=True, language=language):
            self.voice_reply("Hang on, I'm still answering the last one.")

    def voice_reply(self, text):
        """Show the reply in a bubble and, unless voice is off, say it. Then listen for a
        follow-up question for a few seconds, without needing "Hey Buddy" again."""
        self.say(text[:220] + ("…" if len(text) > 220 else ""), max(5000, 70 * len(text)))
        if self.speak and self.speaker.say(text):
            self.listener.hold = True
            self.reply_pending = True        # follow-up opens when he stops talking
        else:
            self.listener.follow_up()

    def _reply_spoken(self):
        if self.reply_pending:
            self.reply_pending = False
            self.listener.hold = self.minutes.recorder.recording
            self.listener.follow_up()

    def on_awaiting(self):
        """Show that he's still listening, under the answer if it's still up."""
        text = self.bubble.text if self.bubble.isVisible() else ""
        if "👂" not in text:
            text = (text + "\n\n" if text else "") + "👂 Anything else?"
        self.say(text, max(self.bubble._hide.remainingTime(), int(CONVERSATION * 1000)))

    def push_to_talk(self):
        if VOICE and not self.listener.isRunning():
            self.listener.start()
        self.listener.push_to_talk()
        self.stand_up()
        self.say("🎤 Listening… go ahead.", 8000)

    def toggle_listen(self):
        self.listen = not self.listen
        self.settings.setValue("voice_listen", self.listen)
        self.listener.enabled = self.listen
        if self.listen and VOICE and not self.listener.isRunning():
            self.listener.start()
        self.say("Say “Hey Buddy” any time." if self.listen else "Okay, I won't listen.", 3000)

    def toggle_speak(self):
        self.speak = not self.speak
        self.settings.setValue("voice_speak", self.speak)
        if not self.speak:
            self.speaker.stop()

    # ---- WhatsApp -----------------------------------------------------------------
    def link_whatsapp(self):
        self.link_dialog = LinkDialog(self.whatsapp)
        self.link_dialog.show()
        self.whatsapp.start()

    def unlink_whatsapp(self):
        self.answer_draft(False)
        self.whatsapp.unlink()
        self.say("WhatsApp unlinked.", 3000)

    def on_draft(self, draft):
        """The assistant wrote a message; it waits on the card for a yes."""
        self.stand_up()
        self.draft_by_voice = self.chat.spoken
        self.draft_card.show_draft(draft)

    def answer_draft(self, yes, spoken=False):
        """Send / Cancel on the card, or "yes" / "no" said or typed while it's up."""
        draft = self.draft_card.draft
        if draft is None:
            return False
        self.draft_by_voice = spoken
        if yes:
            self.draft_card.sending()
            self.whatsapp.send(draft)
        else:
            self.draft_card.hide_card()
            self._tell("Okay, I won't send it.")
        return True

    def on_sent(self, draft, ok, error):
        if self.draft_card.draft == draft:
            self.draft_card.hide_card()
        name = draft.contact.name.split()[0]
        self._tell(f"✔ Sent to {name}." if ok else f"I couldn't send it to {name}: {error}")
        self.chat.note(f"✔ Sent to {draft.contact.name}: {draft.text}" if ok
                       else f"Not sent to {draft.contact.name}: {error}")

    def _tell(self, text, spoken=None):
        """A short answer, out loud as well if the question was spoken."""
        if self.draft_by_voice if spoken is None else spoken:
            self.voice_reply(text)
        else:
            self.say(text, 4000)

    # ---- Claude Code tasks --------------------------------------------------------
    def on_code_proposed(self, task):
        """The assistant wrote down a coding task; it waits on the card for a yes."""
        self.stand_up()
        self.code_by_voice = self.chat.spoken
        self.code_card.ask(task)

    def answer_code(self, yes, spoken=False):
        """Run / Cancel on the card, or "yes" / "no" said or typed while it's up."""
        task = self.code_card.proposal
        if task is None:
            return False
        self.code_by_voice = spoken or self.code_by_voice
        if not yes:
            self.code_card.hide_card()
            self._tell("Okay, I won't start it.", self.code_by_voice)
            return True
        self.launcher.spawn(["code", str(task.folder)], "vscode-" + task.folder.name)
        self.code_card.running(task)
        if self.code.start(task):
            self._tell(f"Starting Claude Code in {task.folder.name}.", self.code_by_voice)
        return True

    def on_code_progress(self, text):
        self.code_card.add_step(text)

    def on_code_finished(self, task, ok, result):
        said = code_summary(result) or ("Done." if ok else "Claude Code stopped.")
        self.code_card.done(ok, said)
        self.chat.note(("✅ Claude Code finished in " if ok else "⚠️ Claude Code stopped in ")
                       + f"{task.folder.name}: {result[:1500]}")
        self.notifier.notify("✅ Claude Code finished" if ok else "⚠️ Claude Code stopped",
                             f"{task.folder.name}: {said}", "wellness", f"code-{task.folder.name}")
        self.stand_up()
        self.wave(4)
        if self.code_by_voice and not self.on_call():
            self.voice_reply(said)
        else:
            self.say(said, max(6000, 60 * len(said)))

    def join_meeting(self, event):
        """Open the meeting's link, stop reminding about it, and offer to take minutes."""
        QDesktopServices.openUrl(QUrl(event.url))
        self.acknowledge(event.key)
        self.minutes.offer(event)

    def open_meeting_link(self, url):
        """The assistant joined a meeting: same as clicking Join."""
        event = next((e for e in self.events if e.url == url), None)
        if event:
            self.join_meeting(event)
        else:
            QDesktopServices.openUrl(QUrl(url))

    def stack_top(self):
        """Top edge of whatever floats above Buddy (card, minutes bar), for the bubble."""
        tops = [w.y() for w in (self.card, self.minutes, self.draft_card, self.code_card)
                if w.isVisible()]
        return min(tops) if tops else self.y() + 10

    def acknowledge(self, key):
        """You've seen this meeting: stop reminding, remove its notification and card."""
        self.schedule.acknowledge(key, datetime.now(UTC))
        self.notifier.close(key)
        if self.card.event and self.card.event.key == key:
            self.card.hide_card()

    def wave(self, seconds):
        now = time.monotonic()
        if now > self.wave_until:
            self.wave_start = now
        self.wave_until = max(self.wave_until, now + seconds)

    def preview_reminder(self):
        self.deliver(Reminder("wellness", WELLNESS_TEXT))
        self.bubble.hide()
        self.check_reminders()

    def preview_meeting(self):
        start = datetime.now(UTC) + timedelta(minutes=10)
        self.schedule.acked.pop("preview", None)
        self.deliver(Reminder("meeting", "", Event("preview", start, "Test meeting (preview)",
                                                   start + timedelta(minutes=30),
                                                   "https://teams.microsoft.com/")))

    # ---- the chair --------------------------------------------------------------
    def char_x(self):
        """Screen x of the middle of the character (the window is wider while seated)."""
        return self.x() + self.pad + WIN_W // 2

    def set_pad(self, pad):
        self.pad = pad
        self.resize(WIN_W + pad, WIN_H)
        self._mask_tick = 0
        self.move(int(self.fx) - pad, int(self.fy))

    def maybe_sit(self, now):
        """No clicks for SIT_AFTER seconds: pull up a chair and sit down."""
        if (self.chair is None and chair.available()
                and now - self.last_click >= SIT_AFTER and self.state == "idle"
                and not self.frozen() and self.wave_start is None and self.fy >= self.floor_y
                and not self.card.isVisible()):
            self.set_pad(CHAIR_PAD)
            self.chair = chair.ChairScene(now)

    def stand_up(self):
        """Get up and put the chair away, reversing from wherever he is in the sequence."""
        now = time.monotonic()
        self.last_click = now
        if self.chair is not None and not self.chair.leaving:
            self.chair.stand_up(now)
            self.state, self.state_until = "idle", now + 3.0

    def end_chair(self):
        if self.chair is not None:
            self.chair = None
            self.set_pad(0)

    def welcome_back(self, seconds_away):
        """You're back at the computer after AWAY_AFTER or more."""
        self.schedule.took_break(datetime.now(UTC))
        self.stand_up()
        minutes = round(seconds_away / 60)
        self.say(f"Welcome back! You were away {minutes} min." if minutes >= 1 else "Welcome back!",
                 4000)
        self.wave(3)

    # ---- behaviour ------------------------------------------------------------
    def frozen(self):
        # XWayland may never report the pointer leaving (it keeps the last position while
        # the pointer is over Wayland windows), so a hover lapses when the pointer goes still.
        hovering = self.hovered and time.monotonic() - self.last_pointer < HOVER_HOLD
        talking = self.speaker.mouth() is not None
        return self.paused or hovering or self.busy or talking or self._drag_offset is not None

    def pick_next(self):
        now = time.monotonic()
        if self.docked:
            if self.state == "walk":
                self.state, self.state_until = "idle", now + random.uniform(5, 10)
                self.facing = -1                     # turn back to face the screen
            else:
                # A few small steps beside the right edge, then back home.
                self.target_x = (max(self.min_x, self.max_x - 36)
                                 if self.fx > self.max_x - 18 else self.max_x)
                self.facing = 1 if self.target_x > self.fx else -1
                self.state = "walk"
            return
        if self.state == "walk" or random.random() < 0.35:
            self.state = "idle"
            self.state_until = now + random.uniform(2.5, 7.0)
        else:
            dist = random.choice([-1, 1]) * random.uniform(150, 600)
            self.target_x = max(self.min_x, min(self.max_x, self.fx + dist))
            if abs(self.target_x - self.fx) < 40:
                self.target_x = self.min_x + self.max_x - self.fx   # at an edge: go the other way
            self.state = "walk"
            self.facing = 1 if self.target_x > self.fx else -1

    def tick(self):
        now = time.monotonic()
        dt = min(now - self._last, 0.05)
        self._last = now
        self.breath += dt * 2.2

        if self._drag_offset is not None:
            pass
        elif self.fy < self.floor_y:                           # dropped from a drag: fall
            self.vy += 2600 * dt
            self.fy = min(self.floor_y, self.fy + self.vy * dt)
            if self.fy >= self.floor_y:
                self.vy = 0
                self.state, self.state_until = "idle", now + 1.0
        elif self.chair is not None:
            if not self.chair.advance(now):
                self.end_chair()
        elif not self.frozen():
            self.maybe_sit(now)
            if self.state == "walk":
                step = (DOCK_SPEED if self.docked else WALK_SPEED) * dt * self.facing
                if abs(self.target_x - self.fx) <= abs(step):
                    self.fx = self.target_x
                    self.pick_next()
                else:
                    self.fx += step
                self.phase += (4.5 if self.docked else STEP_RATE) * dt
            elif now >= self.state_until:
                self.pick_next()
        if self.state != "walk" or self.frozen():
            # settle the walk cycle to a neutral pose instead of snapping
            self.phase = round(self.phase / math.pi) * math.pi if abs(math.sin(self.phase)) < 0.08 \
                else self.phase + STEP_RATE * dt

        moving = self.state == "walk" and not self.frozen()
        target_yaw = self.facing * (0.35 if self.docked else 0.95) if moving else -0.22
        if not moving and not self.frozen():
            target_yaw += math.sin(self.breath * 0.45) * 0.09
        self.yaw += (target_yaw - self.yaw) * min(1.0, dt * 5)
        if now > self.wave_until:
            self.wave_start = None

        self.render_frame()
        self.move(int(self.fx) - self.pad, int(self.fy))
        self.bubble.follow(self)
        self.card.follow(self)
        self.minutes.follow(self)
        self.draft_card.follow(self)
        self.code_card.follow(self)
        self.update()

    # ---- painting -------------------------------------------------------------
    def render_frame(self):
        img = QImage(self.width(), self.height(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        walking = self.state == "walk" and not self.frozen()
        if self.chair is not None:
            anim, frame, alpha = self.chair.frame(time.monotonic())
            model3d.draw_sitting(p, self.width(), self.height(), anim, frame, alpha,
                                 self.breath, self.pad + WIN_W / 2)
        else:
            wave_t = time.monotonic() - self.wave_start if self.wave_start is not None else None
            model3d.draw_model(p, self.width(), self.height(), self.phase, walking, self.breath,
                               self.yaw, wave_t, self.pad + WIN_W / 2, self.speaker.mouth())
        p.end()
        self._frame = img
        # Only the character's own pixels catch the mouse; clicks elsewhere reach the
        # windows underneath. Refreshed a few times a second, grown to cover the motion.
        self._mask_tick -= 1
        if self._mask_tick <= 0 and self._drag_offset is None:
            self._mask_tick = 4
            region = QRegion(QBitmap.fromImage(img.createAlphaMask()))
            grown = QRegion(region)
            for dx, dy in ((-6, 0), (6, 0), (0, -6), (0, 6)):
                grown = grown.united(region.translated(dx, dy))
            self.setMask(grown)

    def paintEvent(self, _):
        if self._frame is not None:
            QPainter(self).drawImage(0, 0, self._frame)

    def say(self, text, ms=3000):
        self.bubble.show_text(text, ms)
        self.bubble.follow(self)

    # ---- mouse ----------------------------------------------------------------
    def enterEvent(self, _):
        self.hovered = True
        self.last_pointer = time.monotonic()

    def leaveEvent(self, _):
        self.hovered = False

    def mousePressEvent(self, e):
        self.stand_up()
        self.speaker.stop()             # a click interrupts him
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.globalPosition().toPoint()

    def mouseMoveEvent(self, e):
        self.last_pointer = time.monotonic()
        if self._press is None or self.docked:
            return
        pos = e.globalPosition().toPoint()
        if self._drag_offset is None and (pos - self._press).manhattanLength() > 6:
            self.end_chair()                     # picked up off the chair
            self._drag_offset = self._press - QPoint(int(self.fx), int(self.fy))
            self.clearMask()
        if self._drag_offset is not None:
            top_left = pos - self._drag_offset
            self.fx = float(max(self.min_x, min(self.max_x, top_left.x())))
            self.fy = float(min(self.floor_y, top_left.y()))

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if self._drag_offset is not None:
            self._drag_offset = None
            self.vy = 0
            if self.fy >= self.floor_y:
                self.say("Wheee!", 1500)
        elif self._press is not None:
            self.open_chat()
        self._press = None

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.addAction("💬  Ask Buddy…", self.open_chat)
        menu.addAction("✍️  Fix text…", self.open_fixer)
        menu.addAction("▶  Resume walking" if self.paused else "⏸  Stay here", self.toggle_pause)
        dock = menu.addAction("Keep on right side", self.toggle_dock)
        dock.setCheckable(True); dock.setChecked(self.docked)

        menu.addSeparator()
        menu.addAction("🎤  Talk to Buddy", self.push_to_talk)
        listen = menu.addAction("Listen for “Hey Buddy”", self.toggle_listen)
        listen.setCheckable(True); listen.setChecked(self.listen)
        speak = menu.addAction("Speak replies", self.toggle_speak)
        speak.setCheckable(True); speak.setChecked(self.speak)
        voice = menu.addAction("Voice: " + self.voice_state)
        voice.setEnabled(False)
        menu.addSeparator()
        if self.whatsapp.state == "not linked":
            menu.addAction("💬  Link WhatsApp…", self.link_whatsapp)
        else:
            wa = menu.addAction("WhatsApp: " + self.whatsapp.state)
            wa.setEnabled(False)
            if self.whatsapp.state in ("stopped", "linking"):
                menu.addAction("💬  Reconnect WhatsApp…", self.link_whatsapp)
            menu.addAction("Unlink WhatsApp", self.unlink_whatsapp)
        menu.addSeparator()
        wellness = menu.addAction("Hourly water + movement reminders", self.toggle_wellness)
        wellness.setCheckable(True); wellness.setChecked(self.wellness)
        menu.addAction("Preview wellness reminder", self.preview_reminder)

        menu.addSeparator()
        meetings = menu.addMenu("📅  Upcoming meetings")
        now = datetime.now(UTC)
        items = upcoming(self.events, now)
        if not items:
            none = meetings.addAction("No meetings in the next day" if self.calendar_ok
                                      else "Calendar not synced yet")
            none.setEnabled(False)
        for ev in items:
            day = "" if ev.start.astimezone().date() == datetime.now().date() else \
                ev.start.astimezone().strftime("%a ")
            label = f"{day}{local_time(ev.start)}  {ev.title[:48]}" + \
                ("   ▶ Join" if ev.url else "   (no link: open in Outlook)")
            action = meetings.addAction(label)
            if ev.url:
                action.triggered.connect(lambda _=False, e=ev: self.join_meeting(e))
            else:
                action.triggered.connect(lambda _=False: QDesktopServices.openUrl(QUrl(self.outlook_url())))
        if self.minutes.recorder.recording:
            menu.addAction("⏹  Stop minutes", self.minutes.stop)
        else:
            menu.addAction("📝  Take minutes now", self.start_minutes_now)
        brief = menu.addAction("Brief me before meetings", self.toggle_brief)
        brief.setCheckable(True); brief.setChecked(self.brief)
        menu.addAction("Preview meeting reminder", self.preview_meeting)
        menu.addAction("Connect Outlook calendar…", self.configure_outlook)
        menu.addAction("Sync calendar now", self.sync_calendar)
        status = menu.addAction("Outlook: " + self.calendar_status)
        status.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Quit", self.quit_safely)
        menu.exec(e.globalPos())

    def start_minutes_now(self):
        now = datetime.now(UTC)
        live = next((e for e in self.events if e.start - timedelta(minutes=5) <= now <= e.end), None)
        self.minutes.start(live, None if live else "Meeting")

    def quit_safely(self):
        if ((self.calendar_worker and self.calendar_worker.isRunning()) or
                any(p.worker and p.worker.isRunning() for p in (self.fixer, self.chat))):
            self.say("Finishing the current request before closing…", 3000)
            after(self, 500, self.quit_safely)
            return
        self.minutes.shutdown()         # finishes the audio; minutes resume at the next start
        self.whatsapp.stop()
        self.code.stop()
        self.speaker.stop()
        if self.listener.isRunning():
            self.listener.stop()
        QApplication.quit()

    def toggle_pause(self):
        self.paused = not self.paused
        self.say("Okay, I'll wait here." if self.paused else "Let's go!", 1800)

    def open_chat(self):
        self.busy = True
        self.bubble.hide()
        self.chat.show_near(self)

    def open_fixer(self):
        self.busy = True
        self.bubble.hide()
        self.fixer.show_near(self)

    def panel_closed(self):
        self.busy = False
        self.state, self.state_until = "idle", time.monotonic() + 1.5


class BriefWorker(QThread):
    """Gather notes and ask Claude for a briefing, off the UI thread."""

    ready = pyqtSignal(str, str)         # meeting key, briefing

    def __init__(self, event, memory):
        super().__init__()
        self.event, self.memory = event, memory

    def run(self):
        from ..services import agent as agent_mod
        notes = gather(self.event, self.memory)
        if not notes or not agent_mod.available():
            return
        try:
            text = write_brief(self.event, notes)
        except Exception as exc:          # a missing brief is fine; just log why
            print(f"briefing failed: {agent_mod.friendly_error(exc)}", flush=True)
            return
        if text:
            self.ready.emit(self.event.key, text)

