"""The Blender-made 3D character: plays the pre-rendered frames in assets/model3d/.

The model lives in blender/buddy_model.blend (built by blender/build_model.py). Frames are
rendered at 7 turn angles (walk, idle) and 5 (wave) by blender/render_frames.py and packed
into one strip per animation and angle by blender/pack_frames.py. blender/sit_frames.py adds
pulling up a chair, sitting down and a seated idle (one angle), plus the chair as a separate
layer that is drawn behind him."""

import json
import math
from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QRadialGradient

ASSETS = Path(__file__).resolve().parent / "assets" / "model3d"
WAVE_FPS = 14


@lru_cache(maxsize=1)
def _manifest():
    return json.loads((ASSETS / "manifest.json").read_text())


def available():
    return (ASSETS / "manifest.json").exists()


@lru_cache(maxsize=None)
def _strip(anim, yaw_index):
    info = _manifest()["anims"][anim][str(yaw_index)]
    img = QImage(str(ASSETS / info["file"]))
    return img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied), info["frames"]


def _nearest_yaw(yaw, allowed):
    yaws = _manifest()["yaws"]
    return min(allowed, key=lambda i: abs(yaws[i] - yaw))


def can_sit():
    m = _manifest() if available() else {}
    return "chair" in m and all(a in m["anims"] for a in ("pull", "sit", "seated"))


# The chair sequence: phase -> (animation, frames per second, played backwards)
SIT_PHASES = {
    "fade_in": ("pull", None, False),     # the chair appears beside him
    "pull": ("pull", 14, False),
    "sit": ("sit", 14, False),
    "seated": ("seated", 6, False),       # loops until he's disturbed
    "stand": ("sit", 18, True),
    "push": ("pull", 18, True),
    "fade_out": ("pull", None, True),     # the chair disappears
}
FADE = 0.4


def phase_length(phase):
    anim, fps, _ = SIT_PHASES[phase]
    if fps is None:
        return FADE
    return float("inf") if phase == "seated" else _strip(anim, _manifest()["sit_yaw_index"])[1] / fps


def phase_time_for_frame(phase, frame):
    """Seconds into phase at which it shows frame (to reverse halfway through)."""
    anim, fps, backwards = SIT_PHASES[phase]
    count = _strip(anim, _manifest()["sit_yaw_index"])[1]
    return ((count - 1 - frame) if backwards else frame) / fps


def phase_frame(phase, t):
    anim, fps, backwards = SIT_PHASES[phase]
    count = _strip(anim, _manifest()["sit_yaw_index"])[1]
    if fps is None:
        return 0
    if phase == "seated":
        return int(t * fps) % count
    frame = min(count - 1, int(t * fps))
    return count - 1 - frame if backwards else frame


def _setup(p, height, walking, breath):
    m = _manifest()
    ground, top = m["ground_y"], m["top_y"]
    scale = (height - 14) / (ground - top)
    breathe = 1 + math.sin(breath) * 0.006 if not walking else 1.0
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    return scale, breathe


def _shadow(p, x, height, w):
    shadow = QRadialGradient(QPointF(x, height - 8), w / 2)
    shadow.setColorAt(0, QColor(10, 16, 30, 90))
    shadow.setColorAt(1, QColor(10, 16, 30, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(shadow)
    p.drawEllipse(QRectF(x - w / 2, height - 15, w, 14))


def draw_sitting(p, width, height, phase, t, breath, cx=None):
    """The chair sequence, character bottom-centred at cx; t is seconds into phase."""
    m = _manifest()
    cx = width / 2 if cx is None else cx
    anim = SIT_PHASES[phase][0]
    yi = m["sit_yaw_index"]
    strip, _ = _strip(anim, yi)
    frame = phase_frame(phase, t)
    fw, fh = m["frame_size"]
    ground = m["ground_y"]
    scale, breathe = _setup(p, height, False, breath)

    chair = m["chair"]
    cw, ch = chair["frame_size"]
    dx, dy = chair["pull_offsets"][frame] if anim == "pull" else (0.0, 0.0)
    alpha = 1.0
    if phase == "fade_in":
        alpha = min(1.0, t / FADE)
    elif phase == "fade_out":
        alpha = max(0.0, 1 - t / FADE)
    p.setOpacity(alpha * 0.8)
    _shadow(p, cx + dx * scale, height, 95 * scale)
    p.setOpacity(1.0)
    _shadow(p, cx, height, 118 * scale)

    p.translate(cx, height - 6)
    p.scale(scale, scale)
    p.setOpacity(alpha)
    p.drawImage(QRectF(-cw / 2 + dx, -ground + dy, cw, ch), _image(chair["file"]))
    p.setOpacity(1.0)
    p.scale(1, breathe if anim == "seated" else 1)
    p.drawImage(QRectF(-fw / 2, -ground, fw, fh), strip, QRectF(frame * fw, 0, fw, fh))
    p.restore()


@lru_cache(maxsize=None)
def _image(name):
    return QImage(str(ASSETS / name)).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def draw_model(p, width, height, phase, walking, breath, yaw, wave_t=None, cx=None):
    """Draw the character bottom-centred at cx (default: the middle of a width x height area).
    phase: walk cycle angle; yaw: turn in radians (+ faces right); wave_t: seconds into a
    wave, or None."""
    m = _manifest()
    cx = width / 2 if cx is None else cx
    fw, fh = m["frame_size"]
    if wave_t is not None and "wave" in m["anims"]:
        anim = "wave"
        yi = _nearest_yaw(yaw, [int(k) for k in m["anims"]["wave"]])
        strip, count = _strip(anim, yi)
        frame = int(wave_t * WAVE_FPS) % count
    elif walking:
        yi = _nearest_yaw(yaw, range(len(m["yaws"])))
        strip, count = _strip("walk", yi)
        frame = int((phase % (2 * math.pi)) / (2 * math.pi) * count) % count
    else:
        yi = _nearest_yaw(yaw, range(len(m["yaws"])))
        strip, count = _strip("idle", yi)
        frame = 0

    ground = m["ground_y"]
    scale, breathe = _setup(p, height, walking, breath)
    _shadow(p, cx, height, 118 * scale)

    # frame space: origin at the shoe soles, centred
    p.translate(cx, height - 6)
    p.scale(scale, scale * breathe)
    p.drawImage(QRectF(-fw / 2, -ground, fw, fh), strip, QRectF(frame * fw, 0, fw, fh))
    p.restore()
