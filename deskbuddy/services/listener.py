"""Hearing "Hey Buddy": cutting the mic stream into utterances and spotting the wake phrase.

Everything here runs on this computer, and nothing is kept: audio lives in memory only
until it's been checked.

1. Endpointer: an adaptive energy gate finds where speech starts and stops (the noise
   floor follows the room, so a fan or AC doesn't count as speech).
2. WakeListener: each utterance goes to the tiny English Whisper model, which is fast.
   If it starts with the wake phrase, the command is transcribed properly with the
   multilingual model, either from the same breath ("Hey Buddy, what's next?") or
   from the next thing said within a few seconds ("Hey Buddy." … "What's next?").
   After he answers, the next few seconds count as a follow-up question too, so a
   conversation doesn't need "Hey Buddy" every time; "thanks" or silence ends it.

Transcribers are passed in, so this logic is testable without audio or models."""

import re
from difflib import SequenceMatcher

import numpy as np

RATE = 16000
FRAME = 480                       # 30 ms
FOLLOW_UP = 8.0                   # seconds to say the command after a bare "Hey Buddy"
CONVERSATION = 10.0               # seconds to ask a follow-up after he's answered

_WAKE = re.compile(r"\b(hey|hi|hay|hei|jay|a|ok|okay)[\s,.!]+(buddy|buddie|budy|bady|body|buddi|bodhi|birdie)\b")


def is_return_command(text):
    """Recognize the explicit local return command, with optional greeting/punctuation."""
    words = " ".join(re.findall(r"\w+", text.lower()))
    return bool(re.fullmatch(r"(?:(?:hey|hi|okay|ok) )?(?:buddy )?come back(?: please)?", words))


def is_talk_command(text):
    words = " ".join(re.findall(r"\w+", text.lower().replace("’", "").replace("'", "")))
    return bool(re.fullmatch(r"(?:(?:hey|hi|okay|ok) )?(?:buddy )?(?:lets|let us) talk(?: please)?", words))


def _words(text):
    return " ".join(re.findall(r"\w+", text.lower().replace("’", "").replace("'", "")))


_POLITE = r"(?:(?:hey|hi|ok|okay|buddy|please|can you|could you|would you|will you) )*"
_MEETING = r"(?:(?:the|this|my|our|a) )?(?:meeting|call|session|screen)"
_MINUTES = r"(?:the )?(?:minutes|mom|notes)(?: (?:of|for) (?:the |this )?meeting)?"
_RECORD = re.compile(
    _POLITE + r"(?:"
    r"(?:start |begin )?(?:recording|record|to record) " + _MEETING +
    r"|(?:start|begin) (?:the )?recording(?: " + _MEETING + r")?"
    r"|(?:take|start|start taking|write|record) " + _MINUTES +
    r")(?: and (?:write|take|make|do|prepare) " + _MINUTES + r")?(?: (?:please|now|for me))*")
_STOP = re.compile(
    _POLITE + r"(?:stop|end|finish) (?:the )?(?:recording|minutes)(?: " + _MEETING + r")?"
    r"(?: (?:please|now))*")


def is_record_command(text):
    """"record the meeting", "start recording", "take minutes", "record this call and write
    the minutes", ... Spoken after the wake phrase, so "buddy" isn't needed."""
    return bool(_RECORD.fullmatch(_words(text)))


def is_stop_record_command(text, need_name=False):
    """"stop recording", "end the minutes". need_name: while recording, only
    "buddy stop recording" counts, so someone in the meeting saying it doesn't."""
    words = _words(text)
    return bool(_STOP.fullmatch(words)) and (not need_name or "buddy" in words.split())


def normalize(text):
    """Lower-case, punctuation (other than , . !) to spaces. Same length as text, so
    positions found here are positions in the original."""
    return "".join(c if (c.isalnum() or c.isspace() or c in ",.!") else " " for c in text.lower())


def find_wake(text):
    """(start, end) of the wake phrase if it's among the first few words, else None."""
    t = normalize(text)
    m = _WAKE.search(t)
    if m and len(t[:m.start()].split()) <= 2:
        return m.span()
    words = [(w.start(), w.end()) for w in re.finditer(r"[^\s,.!]+", t)]
    best = (0.82, None)                               # fuzzy: "heybuddy", "hay bud e"
    for i in range(min(3, len(words))):
        for n in (1, 2, 3):
            if i + n > len(words):
                break
            candidate = "".join(t[a:b] for a, b in words[i:i + n])
            ratio = SequenceMatcher(None, candidate, "heybuddy").ratio()
            if ratio >= best[0]:
                best = (ratio, (words[i][0], words[i + n - 1][1]))
    return best[1]


def strip_wake(text):
    """The command part of 'Hey Buddy, what's my next meeting?'."""
    span = find_wake(text)
    return (text[span[1]:] if span else text).lstrip(" ,.!?").strip()


_GOODBYE = re.compile(r"^((no|nope|nothing|stop|cancel|bye|goodbye|good bye|thats all|thats it|that is all|"
                      r"ok|okay|alright|all right|cool|great|got it|perfect)( |$))*"
                      r"((no )?thanks?( you)?|bye)?( buddy)?$")


def is_goodbye(text):
    """"Thanks", "that's all", "no": the end of a conversation, not a question."""
    words = normalize(text.replace("'", "").replace("’", "")).replace(",", " ").replace(".", " ")
    words = " ".join(words.replace("!", " ").split())
    return bool(words) and bool(_GOODBYE.match(words))


class Endpointer:
    """Feed 30 ms int16 frames; get back finished utterances (float32 arrays)."""

    START_FRAMES = 5              # 150 ms above the gate …
    START_WINDOW = 8              # … within 240 ms to start (speech dips between sounds)
    END_FRAMES = 25               # 750 ms below it to finish
    PRE_ROLL = 10                 # keep 300 ms before the start
    GATE = 3.0                    # speech starts this many times louder than the room
    MIN_FRAMES = 12               # ignore blips under 360 ms
    MAX_FRAMES = 400              # cut at 12 s

    def __init__(self):
        self.floor = 0.003
        self.frames, self.pre, self.levels = [], [], []
        self.active, self.recent, self.quiet = False, [], 0

    def len_cut(self):
        return len(self.frames) >= self.MAX_FRAMES

    @property
    def too_loud(self):
        """The room alone is this loud: the mic gain is far too high to hear words."""
        return self.floor > 0.1

    def feed(self, frame):
        audio = frame.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(audio ** 2))) if len(audio) else 0.0
        start_gate = max(self.floor * self.GATE, 0.004)
        if not self.active:
            self.pre = (self.pre + [audio])[-self.PRE_ROLL:]
            loud = rms > start_gate
            self.recent = (self.recent + [loud])[-self.START_WINDOW:]
            if sum(self.recent) >= self.START_FRAMES:
                self.active, self.frames, self.quiet, self.levels = True, list(self.pre), 0, []
                self.recent = []
            elif not loud:
                self.floor = 0.95 * self.floor + 0.05 * rms          # track the room
            return None
        self.frames.append(audio)
        self.levels.append(rms)
        self.quiet = self.quiet + 1 if rms < max(self.floor * 2.0, 0.003) else 0
        if self.len_cut():
            # 12 s without a pause is the room, not a sentence (e.g. the mic gain jumped
            # after a reboot): learn the new floor from it, or we'd never find a pause again
            self.floor = max(self.floor, float(np.percentile(self.levels, 20)))
        if self.quiet >= self.END_FRAMES or self.len_cut():
            frames, self.frames, self.active, self.recent, self.pre = self.frames, [], False, [], []
            self.levels = []
            if len(frames) - self.quiet >= self.MIN_FRAMES:
                return np.concatenate(frames)
        return None


class WakeListener:
    def __init__(self, quick, full):
        """quick(audio) -> text: fast English model for the wake phrase.
        full(audio) -> text: multilingual model for the command."""
        self.quick, self.full = quick, full
        self.awaiting_until = 0.0

    def expect_command(self, now, seconds=FOLLOW_UP):
        """The next utterance is a command (after "Hey Buddy." or push-to-talk)."""
        self.awaiting_until = now + seconds

    def on_utterance(self, audio, now, return_only=False, stop_only=False):
        """Returns None, ("wake",) or ("command", text).
        stop_only (while recording minutes): only "buddy stop recording" is heard."""
        if stop_only and not return_only:
            self.awaiting_until = 0.0
            heard = self.quick(audio)
            return ("command", "stop recording") if is_stop_record_command(heard, need_name=True) else None
        if return_only:
            self.awaiting_until = 0.0
            heard = self.quick(audio)
            if is_talk_command(heard):
                return ("command", "let's talk")
            return ("command", "come back") if is_return_command(heard) else None
        if now < self.awaiting_until:
            text = strip_wake(self.full(audio))
            if not text:                  # a cough or a door: keep waiting for the question
                return None
            self.awaiting_until = 0.0
            return ("command", text)
        heard = self.quick(audio)
        if is_talk_command(heard):
            return ("command", "let's talk")
        if is_return_command(heard):
            return ("command", "come back")
        span = find_wake(heard)
        if span is None:
            return None
        rest = heard[span[1]:]
        if len(rest.split()) >= 2:
            command = strip_wake(self.full(audio)) or rest.strip(" ,.!?")
            return ("command", command)
        self.expect_command(now)
        return ("wake",)
