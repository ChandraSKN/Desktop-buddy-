"""Local camera guard policy. No camera or power commands run in this module."""

import re


class GuardPolicy:
    def __init__(self):
        self.state = "watching"
        self.deadline = None
        self.unknown_frames = 0
        self.last_frame = None
        self.empty_since = None

    def observe(self, presence, now):
        self.last_frame = now
        if presence == "empty":
            self.empty_since = now if self.empty_since is None else self.empty_since
            self.unknown_frames = 0
            if self.state != "allowed" or now - self.empty_since >= 5:
                self.reset()
            return
        self.empty_since = None
        if presence == "owner":
            self.reset()
        elif self.state == "watching":
            self.unknown_frames += 1
            if self.unknown_frames >= 3:
                self.state, self.deadline = "challenge", now + 20

    def answer(self, text):
        words = " ".join(re.findall(r"\w+", text.casefold()))
        if self.state in ("challenge", "countdown") and words == "kamal is great":
            self.state, self.deadline = "allowed", None
            return True
        return False

    def reset(self):
        self.state, self.deadline, self.unknown_frames = "watching", None, 0

    def tick(self, now):
        if self.last_frame is None or now - self.last_frame > 5:
            self.reset()
            return "stale"
        if self.deadline is not None and now >= self.deadline:
            if self.state == "challenge":
                self.state, self.deadline = "countdown", now + 30
            elif self.state == "countdown":
                self.state, self.deadline = "finished", None
                return "poweroff"
        return self.state
