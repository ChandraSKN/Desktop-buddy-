"""Desktop Buddy: an animated companion that lives at the bottom of the screen, fixes your
text, reminds you about Outlook meetings, and nudges you to drink water and move."""

import math
import os
import random
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone

# GNOME on Wayland doesn't let apps position their own windows; XWayland does.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PyQt6.QtCore import QPoint, QRectF, QSettings, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QAction, QActionGroup, QBitmap, QColor, QDesktopServices, QFont,
                         QFontMetrics, QGuiApplication, QImage, QPainter, QPainterPath, QPen,
                         QRegion)
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu,
                             QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

import corrector
from avatar import draw_avatar
from figure import draw_figure
import model3d
from notifier import Notifier
from reminders import (WELLNESS_TEXT, CalendarWorker, Event, Reminder, ReminderSchedule,
                       meeting_text, outlook_web_url, upcoming, validate_url)

WALK_SPEED = 75       # px per second when roaming
DOCK_SPEED = 24       # px per second for the small steps while docked
STEP_RATE = 7.0       # radians of walk-cycle per second
FPS = 30
WIN_W, WIN_H = 200, 300
SIT_AFTER = 30        # seconds without a click before he pulls up a chair and sits
HOVER_HOLD = 3.0      # hover counts only this long after the pointer last moved over him
CHAIR_PAD = 90       # extra window width on his right (screen-left) while the chair is out


def local_time(dt):
    return dt.astimezone().strftime("%H:%M")


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
        self.style = self.settings.value("style", "blender")
        if not self.settings.value("blender_model_offered", False, type=bool):
            # one-time switch to the new Blender model; the menu can switch back
            self.settings.setValue("blender_model_offered", True)
            self.style = "blender"
            self.settings.setValue("style", self.style)
        if self.style == "blender" and not model3d.available():
            self.style = "illustrated"

        self.update_geometry(initial=True)
        self.vy = 0.0
        self.facing = -1         # 1 = right, -1 = left (docked on the right, he faces the screen)
        self.flip = -1.0         # animated horizontal scale for the illustrated turn
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
        self.chair = None        # [phase, started] while pulling up / sitting / standing

        self.panel = CorrectorPanel(self)
        self.bubble = Bubble()
        self.card = MeetingCard(self)
        self.notifier = Notifier(self)
        self.notifier.action.connect(self.notification_clicked)

        self.events = []
        self.pending = deque()
        self.calendar_worker = None
        self.calendar_ok = None
        self.calendar_status = "Not connected"
        self.schedule = ReminderSchedule(datetime.now(timezone.utc))
        self.reminder_timer = QTimer(self, timeout=self.check_reminders)
        self.reminder_timer.start(1000)
        self.calendar_timer = QTimer(self, timeout=self.sync_calendar)
        self.calendar_timer.start(5 * 60 * 1000)
        QTimer.singleShot(1500, self.sync_calendar)
        QGuiApplication.primaryScreen().availableGeometryChanged.connect(lambda *_: self.update_geometry())
        QGuiApplication.instance().screenAdded.connect(lambda *_: self.update_geometry())
        QGuiApplication.instance().screenRemoved.connect(lambda *_: self.update_geometry())

        self.timer = QTimer(self, timeout=self.tick)
        self.timer.start(int(1000 / FPS))
        self.move(int(self.fx), int(self.fy))
        QTimer.singleShot(800, lambda: self.say("Hi! Click me to fix your text ✨", 4500))

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
        self.schedule.next_wellness = datetime.now(timezone.utc) + timedelta(hours=1)

    def set_style(self, style):
        self.end_chair()
        self.style = style
        self.settings.setValue("style", style)
        self._frame = None

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
            nxt = upcoming(events, datetime.now(timezone.utc), 1)
            if nxt:
                self.say(f"Calendar connected. Next: {nxt[0].title[:40]} at {local_time(nxt[0].start)}", 6000)
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
        for reminder in self.schedule.due(datetime.now(timezone.utc), self.events, self.wellness):
            self.deliver(reminder)
        if self.pending and not self.busy and not self.bubble.isVisible():
            self.say(self.pending.popleft(), 20000)
            self.wave(6)

    def deliver(self, reminder):
        """Desktop notification right away, plus Buddy's own card or speech bubble.
        Meeting reminders come back every 2 minutes until you press Join or Dismiss."""
        self.stand_up()
        if reminder.event is not None:
            ev = reminder.event
            _, when = meeting_text(ev, datetime.now(timezone.utc))
            body = f"{when[0].upper()}{when[1:]} · {local_time(ev.start)}–{local_time(ev.end)}"
            if ev.url:
                body += f"\n{ev.url}"
                actions = [("join", "Join meeting"), ("dismiss", "Dismiss")]
            else:
                body += "\nNo meeting link in this invite."
                actions = [("outlook", "Open in Outlook"), ("dismiss", "Dismiss")]
            self.notifier.notify(f"📅 {ev.title}", body, "meeting", ev.key, actions)
            self.card.show_event(ev)
            self.wave(12)
        else:
            self.notifier.notify("💧 Time for a break", WELLNESS_TEXT, "wellness")
            self.pending.append(reminder.text)

    def outlook_url(self):
        return outlook_web_url(self.settings.value("calendar_url", ""))

    def notification_clicked(self, key, action):
        event = next((e for e in self.events + [self.card.event] if e and e.key == key), None)
        if action == "join" and event and event.url:
            QDesktopServices.openUrl(QUrl(event.url))
        elif action == "outlook":
            QDesktopServices.openUrl(QUrl(self.outlook_url()))
        if action in ("join", "outlook", "dismiss"):
            self.acknowledge(key)

    def acknowledge(self, key):
        """You've seen this meeting: stop reminding, remove its notification and card."""
        self.schedule.acknowledge(key, datetime.now(timezone.utc))
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
        start = datetime.now(timezone.utc) + timedelta(minutes=10)
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
        if (self.chair is None and self.style == "blender" and model3d.can_sit()
                and now - self.last_click >= SIT_AFTER and self.state == "idle"
                and not self.frozen() and self.wave_start is None and self.fy >= self.floor_y
                and not self.card.isVisible()):
            self.set_pad(CHAIR_PAD)
            self.chair = ["fade_in", now]

    def advance_chair(self, now):
        phase, start = self.chair
        if now - start < model3d.phase_length(phase):
            return
        nxt = {"fade_in": "pull", "pull": "sit", "sit": "seated",
               "stand": "push", "push": "fade_out"}.get(phase)
        if nxt:
            self.chair = [nxt, start + model3d.phase_length(phase)]
        elif phase == "fade_out":
            self.end_chair()

    def stand_up(self):
        """Get up and put the chair away, reversing from wherever he is in the sequence."""
        self.last_click = time.monotonic()
        if self.chair is None or self.chair[0] in ("stand", "push", "fade_out"):
            return
        now = time.monotonic()
        phase, start = self.chair
        t = now - start
        if phase == "fade_in":
            self.chair = ["fade_out", now - (model3d.FADE - t)]
        else:
            back = {"pull": "push", "sit": "stand", "seated": "stand"}[phase]
            frame = model3d.phase_frame(phase, t) if phase != "seated" else \
                model3d.phase_frame("sit", 1e9)
            self.chair = [back, now - model3d.phase_time_for_frame(back, frame)]
        self.state, self.state_until = "idle", now + 3.0

    def end_chair(self):
        if self.chair is not None:
            self.chair = None
            self.set_pad(0)

    # ---- behaviour ------------------------------------------------------------
    def frozen(self):
        # XWayland may never report the pointer leaving (it keeps the last position while
        # the pointer is over Wayland windows), so a hover lapses when the pointer goes still.
        hovering = self.hovered and time.monotonic() - self.last_pointer < HOVER_HOLD
        return self.paused or hovering or self.busy or self._drag_offset is not None

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
            self.advance_chair(now)
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

        self.flip += (self.facing - self.flip) * min(1.0, dt * 10)
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
            model3d.draw_sitting(p, self.width(), self.height(), self.chair[0],
                                 time.monotonic() - self.chair[1], self.breath,
                                 self.pad + WIN_W / 2)
        elif self.style == "blender":
            wave_t = time.monotonic() - self.wave_start if self.wave_start is not None else None
            model3d.draw_model(p, self.width(), self.height(), self.phase, walking, self.breath,
                               self.yaw, wave_t)
        elif self.style == "3d":
            draw_avatar(p, self.width(), self.height(), self.phase, walking, self.breath,
                        self.yaw, self.wave_start is not None)
        else:
            wave_t = time.monotonic() - self.wave_start if self.wave_start is not None else None
            draw_figure(p, self.width(), self.height(), self.phase, walking, self.breath,
                        self.flip, wave_t)
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
            self.open_panel()
        self._press = None

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.addAction("✍️  Fix text…", self.open_panel)
        menu.addAction("▶  Resume walking" if self.paused else "⏸  Stay here", self.toggle_pause)
        dock = menu.addAction("Keep on right side", self.toggle_dock)
        dock.setCheckable(True); dock.setChecked(self.docked)

        look = menu.addMenu("Character")
        group = QActionGroup(look)
        for label, key in (("3D model (Blender)", "blender"), ("Illustrated (artwork)", "illustrated"),
                           ("Simple 3D", "3d")):
            action = look.addAction(label, lambda k=key: self.set_style(k))
            action.setCheckable(True); action.setChecked(self.style == key)
            group.addAction(action)

        menu.addSeparator()
        wellness = menu.addAction("Hourly water + movement reminders", self.toggle_wellness)
        wellness.setCheckable(True); wellness.setChecked(self.wellness)
        menu.addAction("Preview wellness reminder", self.preview_reminder)

        menu.addSeparator()
        meetings = menu.addMenu("📅  Upcoming meetings")
        now = datetime.now(timezone.utc)
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
            link = ev.url or self.outlook_url()
            action.triggered.connect(lambda _=False, u=link: QDesktopServices.openUrl(QUrl(u)))
        menu.addAction("Preview meeting reminder", self.preview_meeting)
        menu.addAction("Connect Outlook calendar…", self.configure_outlook)
        menu.addAction("Sync calendar now", self.sync_calendar)
        status = menu.addAction("Outlook: " + self.calendar_status)
        status.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Quit", self.quit_safely)
        menu.exec(e.globalPos())

    def quit_safely(self):
        if ((self.calendar_worker and self.calendar_worker.isRunning()) or
                (self.panel.worker and self.panel.worker.isRunning())):
            self.say("Finishing the current request before closing…", 3000)
            QTimer.singleShot(500, self.quit_safely)
            return
        QApplication.quit()

    def toggle_pause(self):
        self.paused = not self.paused
        self.say("Okay, I'll wait here." if self.paused else "Let's go!", 1800)

    def open_panel(self):
        self.busy = True
        self.bubble.hide()
        self.panel.show_near(self)

    def panel_closed(self):
        self.busy = False
        self.state, self.state_until = "idle", time.monotonic() + 1.5


class Bubble(QWidget):
    """Little speech bubble that floats above the buddy."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.text = ""
        self.font_ = QFont("Sans", 10)
        self._hide = QTimer(self, singleShot=True, timeout=self.hide)

    def show_text(self, text, ms):
        self.text = text
        screen = QGuiApplication.primaryScreen().availableGeometry()
        fm = QFontMetrics(self.font_)
        w = min(320, screen.width() - 24, fm.horizontalAdvance(text) + 32)
        bounds = fm.boundingRect(0, 0, w - 28, 1000, Qt.TextFlag.TextWordWrap, text)
        self.resize(w, bounds.height() + 32)
        self.show()
        self.update()
        self._hide.start(ms)

    def follow(self, buddy):
        if self.isVisible():
            screen = QGuiApplication.primaryScreen().availableGeometry()
            x = min(screen.right() - self.width() - 8,
                    max(screen.left() + 8, buddy.char_x() - self.width() // 2))
            top = buddy.card.y() if buddy.card.isVisible() else buddy.y() + 10
            self.move(x, max(screen.top() + 8, top - self.height()))
            self._tail_x = buddy.char_x() - x

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 12), 14, 14)
        tail = QPainterPath()
        mid = min(self.width() - 22, max(22, getattr(self, "_tail_x", self.width() / 2)))
        tail.moveTo(mid - 7, self.height() - 13); tail.lineTo(mid, self.height() - 2); tail.lineTo(mid + 7, self.height() - 13)
        path = path.united(tail)
        p.setPen(QPen(QColor(40, 50, 90), 1.5))
        p.setBrush(QColor(255, 255, 255, 245))
        p.drawPath(path)
        p.setFont(self.font_)
        p.setPen(QColor(30, 30, 40))
        p.drawText(QRectF(14, 6, self.width() - 28, self.height() - 24),
                   Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self.text)


CARD_CSS = """
QWidget#card { background: #1d2233; border: 1px solid #5b73e8; border-radius: 14px; }
QLabel { color: #e8ecf8; }
QLabel#when { color: #9fb2ff; font-size: 11px; font-weight: 600; }
QLabel#title { font-size: 14px; font-weight: 600; }
QLabel#time { color: #9aa6c8; font-size: 11px; }
QLabel#link { color: #8fb4ff; font-size: 11px; }
QLabel#nolink { color: #c9b27a; font-size: 11px; }
QPushButton { background: #2c3450; color: #e8ecf8; border: none; border-radius: 8px;
              padding: 6px 11px; font-size: 12px; }
QPushButton:hover { background: #3a4466; }
QPushButton#join { background: #5b73e8; font-weight: 600; }
QPushButton#join:hover { background: #6d86f0; }
"""


class MeetingCard(QWidget):
    """A meeting reminder that stays above Buddy until you Join/Dismiss (or it ends)."""

    def __init__(self, buddy):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(CARD_CSS)
        self.buddy = buddy
        self.event = None
        self.setFixedWidth(320)

        card = QWidget(self, objectName="card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 12)
        lay.setSpacing(4)
        self.when = QLabel(objectName="when")
        self.title = QLabel(objectName="title", wordWrap=True)
        self.time = QLabel(objectName="time")
        self.link = QLabel(objectName="link", openExternalLinks=True,
                           textInteractionFlags=Qt.TextInteractionFlag.TextBrowserInteraction)
        self.nolink = QLabel("No meeting link in this invite.", objectName="nolink")
        for w in (self.when, self.title, self.time, self.link, self.nolink):
            lay.addWidget(w)
        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 0)
        self.copy = QPushButton("Copy link", clicked=self.copy_link)
        row.addWidget(self.copy)
        row.addStretch()
        row.addWidget(QPushButton("Dismiss", clicked=self.dismiss))
        self.join = QPushButton("Join meeting", objectName="join", clicked=self.open_link)
        row.addWidget(self.join)
        lay.addLayout(row)

        self._tick = QTimer(self, timeout=self.refresh)

    def show_event(self, event):
        self.event = event
        has_link = bool(event.url)
        self.join.setText("Join meeting" if has_link else "Open in Outlook")
        self.copy.setVisible(has_link)
        self.link.setVisible(has_link)
        self.nolink.setVisible(not has_link)
        if has_link:
            shown = event.url if len(event.url) <= 48 else event.url[:45] + "…"
            self.link.setText(f'<a href="{event.url}" style="color:#8fb4ff">{shown}</a>')
        self.title.setText(event.title)
        self.time.setText(f"{local_time(event.start)} – {local_time(event.end)}")
        self.refresh()
        self.adjustSize()
        self.show()
        self.raise_()
        self.follow(self.buddy)
        self._tick.start(15000)

    def refresh(self):
        if self.event is None:
            return
        now = datetime.now(timezone.utc)
        if now >= self.event.end:
            self.hide_card()
            return
        kind, when = meeting_text(self.event, now)
        self.when.setText(f"📅  MEETING {when.upper()}")

    def follow(self, buddy):
        if self.isVisible():
            screen = QGuiApplication.primaryScreen().availableGeometry()
            x = min(screen.right() - self.width() - 8,
                    max(screen.left() + 8, buddy.char_x() - self.width() // 2))
            self.move(x, max(screen.top() + 8, buddy.y() + 4 - self.height()))

    def copy_link(self):
        if self.event and self.event.url:
            QApplication.clipboard().setText(self.event.url)
            self.copy.setText("Copied ✔")
            QTimer.singleShot(1500, lambda: self.copy.setText("Copy link"))

    def open_link(self):
        if self.event:
            QDesktopServices.openUrl(QUrl(self.event.url or self.buddy.outlook_url()))
            self.buddy.acknowledge(self.event.key)

    def dismiss(self):
        if self.event:
            self.buddy.acknowledge(self.event.key)
        else:
            self.hide_card()

    def hide_card(self):
        self._tick.stop()
        self.event = None
        self.hide()
        self.buddy.wave_until = 0


class Worker(QThread):
    done = pyqtSignal(str, list)
    failed = pyqtSignal(str)

    def __init__(self, text):
        super().__init__()
        self.text = text

    def run(self):
        try:
            self.done.emit(*corrector.correct(self.text))
        except Exception as e:
            self.failed.emit(friendly_error(e))


def friendly_error(e):
    try:
        import anthropic
        if isinstance(e, anthropic.AuthenticationError):
            return "Your Claude API key was rejected. Check ~/.config/desktop-buddy/api_key."
        if isinstance(e, anthropic.RateLimitError):
            return "Rate limited — wait a moment and try again."
        if isinstance(e, anthropic.APIConnectionError):
            return "Couldn't reach Claude. Are you online?"
        if isinstance(e, anthropic.APIStatusError):
            return f"Claude API error {e.status_code}: {e.message}"
    except ImportError:
        pass
    return f"Something went wrong: {e}"


PANEL_CSS = """
QWidget#card { background: #1d2233; border: 1px solid #3a4466; border-radius: 16px; }
QLabel { color: #e8ecf8; }
QLabel#title { font-size: 15px; font-weight: 600; }
QLabel#status { color: #9aa6c8; font-size: 11px; }
QPlainTextEdit { background: #11141f; color: #eef1fb; border: 1px solid #333c5c;
                 border-radius: 10px; padding: 6px; font-size: 13px;
                 selection-background-color: #4f6bd8; }
QPlainTextEdit:focus { border-color: #6d86f0; }
QPlainTextEdit#changes { color: #b9c3e6; font-size: 12px; }
QPushButton { background: #2c3450; color: #e8ecf8; border: none; border-radius: 9px;
              padding: 7px 14px; font-size: 12px; }
QPushButton:hover { background: #3a4466; }
QPushButton#primary { background: #5b73e8; font-weight: 600; }
QPushButton#primary:hover { background: #6d86f0; }
QPushButton#primary:disabled { background: #3b4677; color: #b8c0dc; }
QPushButton#close { background: transparent; font-size: 16px; padding: 2px 8px; }
QPushButton#close:hover { background: #3a2230; color: #ff8fa3; }
"""


class CorrectorPanel(QWidget):
    def __init__(self, buddy):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.buddy = buddy
        self.worker = None
        self._drag = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(PANEL_CSS)
        self.resize(480, 620)

        card = QWidget(self, objectName="card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(QLabel("✍️  Fix my text", objectName="title"))
        head.addStretch()
        close = QPushButton("✕", objectName="close", clicked=self.close)
        head.addWidget(close)
        lay.addLayout(head)

        self.input = QPlainTextEdit(placeholderText="Paste the text you want corrected…   (Ctrl+Enter to fix)")
        lay.addWidget(self.input, 3)

        row = QHBoxLayout()
        row.addWidget(QPushButton("📋 Paste", clicked=self.paste))
        row.addWidget(QPushButton("Clear", clicked=lambda: (self.input.clear(), self.output.clear(), self.changes.clear())))
        row.addStretch()
        self.fix_btn = QPushButton("Fix it ✨", objectName="primary", clicked=self.run_fix)
        row.addWidget(self.fix_btn)
        lay.addLayout(row)

        self.output = QPlainTextEdit(readOnly=True, placeholderText="Corrected text will appear here.")
        lay.addWidget(self.output, 3)

        self.changes_title = QLabel("What I fixed", objectName="status")
        lay.addWidget(self.changes_title)
        self.changes = QPlainTextEdit(readOnly=True, objectName="changes",
                                      placeholderText="An explanation of each correction will appear here.")
        lay.addWidget(self.changes, 2)

        row2 = QHBoxLayout()
        self.status = QLabel("", objectName="status")
        row2.addWidget(self.status, 1)
        self.copy_btn = QPushButton("Copy", clicked=self.copy)
        row2.addWidget(self.copy_btn)
        lay.addLayout(row2)

        self.addAction(QAction(self, shortcut="Ctrl+Return", triggered=self.run_fix))
        self.addAction(QAction(self, shortcut="Escape", triggered=self.close))

    def show_near(self, buddy):
        scr = QGuiApplication.primaryScreen().availableGeometry()
        x = buddy.x() + buddy.width() if buddy.x() + buddy.width() + self.width() < scr.right() \
            else buddy.x() - self.width()
        y = scr.bottom() - self.height() - 40
        self.move(max(scr.left(), x), max(scr.top(), y))
        self.status.setText(f"Using {corrector.backend_name()}")
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()

    def paste(self):
        self.input.setPlainText(QApplication.clipboard().text())
        self.input.setFocus()

    def run_fix(self):
        text = self.input.toPlainText().strip()
        if not text or (self.worker and self.worker.isRunning()):
            return
        self.fix_btn.setEnabled(False)
        self.fix_btn.setText("Thinking…")
        self.status.setText(f"Correcting with {corrector.backend_name()}…")
        self.output.clear()
        self.changes.clear()
        self.worker = Worker(text)
        self.worker.done.connect(self.on_done)
        self.worker.failed.connect(self.on_failed)
        self.worker.start()

    def on_done(self, fixed, changes):
        self.output.setPlainText(fixed)
        self.changes.setPlainText("\n".join(f"• {c}" for c in changes))
        self.fix_btn.setEnabled(True)
        self.fix_btn.setText("Fix it ✨")
        same = fixed.strip() == self.input.toPlainText().strip()
        if same and not changes:
            self.status.setText("Looks good already, nothing to fix 👍")
        else:
            n = len(changes)
            self.status.setText(f"{n} fix{'es' if n != 1 else ''} made. Click Copy to use it.")

    def on_failed(self, msg):
        self.fix_btn.setEnabled(True)
        self.fix_btn.setText("Fix it ✨")
        self.status.setText(msg)

    def copy(self):
        if self.output.toPlainText():
            QApplication.clipboard().setText(self.output.toPlainText())
            self.status.setText("Copied to clipboard ✔")

    # drag the panel by its header
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < 48:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        self._drag = None

    def closeEvent(self, e):
        super().closeEvent(e)
        self.buddy.panel_closed()
        self.buddy.say("Happy to help!", 2000)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Desktop Buddy")
    buddy = Buddy()
    buddy.show()
    # `systemctl --user kill -s USR1 desktop-buddy` prints his state to the journal
    import signal
    signal.signal(signal.SIGUSR1, lambda *_: print(
        f"state={buddy.state} chair={buddy.chair and buddy.chair[0]} paused={buddy.paused} "
        f"hovered={buddy.hovered} busy={buddy.busy} drag={buddy._drag_offset is not None} "
        f"wave={buddy.wave_start is not None} card={buddy.card.isVisible()} "
        f"style={buddy.style} fy={buddy.fy} floor={buddy.floor_y} "
        f"since_click={time.monotonic() - buddy.last_click:.0f}s", flush=True))
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
