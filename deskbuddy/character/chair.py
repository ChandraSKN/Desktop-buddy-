"""The chair sequence: pull a chair up, sit, stay seated, stand up, push it away.

Pure timing, no drawing: ChairScene says which animation frame to show and how visible
the chair is; model3d.draw_sitting() paints it. Frame counts come from the rendered
strips (blender/sit_frames.py)."""

from . import model3d

# phase -> (animation, frames per second, played backwards)
PHASES = {
    "fade_in": ("pull", None, False),     # the chair appears beside him
    "pull": ("pull", 14, False),
    "sit": ("sit", 14, False),
    "seated": ("seated", 6, False),       # loops until he's disturbed
    "stand": ("sit", 18, True),
    "push": ("pull", 18, True),
    "fade_out": ("pull", None, True),     # the chair disappears
}
NEXT = {"fade_in": "pull", "pull": "sit", "sit": "seated", "stand": "push", "push": "fade_out"}
REVERSE = {"pull": "push", "sit": "stand", "seated": "stand"}
FADE = 0.4


def available():
    return model3d.can_sit()


def phase_length(phase):
    anim, fps, _ = PHASES[phase]
    if fps is None:
        return FADE
    return float("inf") if phase == "seated" else model3d.sit_frame_count(anim) / fps


def phase_frame(phase, t):
    """The frame of phase's animation shown t seconds into it."""
    anim, fps, backwards = PHASES[phase]
    count = model3d.sit_frame_count(anim)
    if fps is None:
        return 0
    if phase == "seated":
        return int(t * fps) % count
    frame = min(count - 1, int(t * fps))
    return count - 1 - frame if backwards else frame


def phase_time_for_frame(phase, frame):
    """Seconds into phase at which it shows frame (used to reverse halfway through)."""
    anim, fps, backwards = PHASES[phase]
    count = model3d.sit_frame_count(anim)
    return ((count - 1 - frame) if backwards else frame) / fps


class ChairScene:
    def __init__(self, now):
        self.phase, self.start = "fade_in", now

    @property
    def leaving(self):
        return self.phase in ("stand", "push", "fade_out")

    def advance(self, now):
        """Move on to the next phase when this one is over. False once the chair is gone."""
        while now - self.start >= phase_length(self.phase):
            if self.phase == "fade_out":
                return False
            self.start += phase_length(self.phase)
            self.phase = NEXT[self.phase]
        return True

    def stand_up(self, now):
        """Reverse from wherever he is: get up and put the chair away."""
        if self.leaving:
            return
        t = now - self.start
        if self.phase == "fade_in":
            self.phase, self.start = "fade_out", now - (FADE - t)
            return
        back = REVERSE[self.phase]
        frame = phase_frame(self.phase, t) if self.phase != "seated" else \
            model3d.sit_frame_count("sit") - 1
        self.phase, self.start = back, now - phase_time_for_frame(back, frame)

    def frame(self, now):
        """(animation, frame index, chair opacity) to draw now."""
        t = now - self.start
        anim = PHASES[self.phase][0]
        alpha = 1.0
        if self.phase == "fade_in":
            alpha = min(1.0, t / FADE)
        elif self.phase == "fade_out":
            alpha = max(0.0, 1 - t / FADE)
        return anim, phase_frame(self.phase, t), alpha
