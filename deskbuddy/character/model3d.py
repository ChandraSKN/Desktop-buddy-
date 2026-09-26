"""The Blender-made 3D character: plays the pre-rendered frames in assets/model3d/.

The model lives in blender/buddy_model.blend (built by blender/build_model.py). Frames are
rendered at 7 turn angles (walk, idle) and 5 (wave) by blender/render_frames.py and packed
into one strip per animation and angle by blender/pack_frames.py. blender/sit_frames.py adds
pulling up a chair, sitting down and a seated idle (one angle), plus the chair as a separate
layer that is drawn behind him."""

import json
import math
from functools import cache, lru_cache

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QRadialGradient

from ..config import ASSETS as _ASSETS_ROOT

ASSETS = _ASSETS_ROOT / "model3d"
WAVE_FPS = 14


@lru_cache(maxsize=1)
def _manifest():
    return json.loads((ASSETS / "manifest.json").read_text())


def available():
    return (ASSETS / "manifest.json").exists()


@cache
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


def sit_frame_count(anim):
    return _strip(anim, _manifest()["sit_yaw_index"])[1]


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


def draw_sitting(p, width, height, anim, frame, chair_alpha, breath, cx=None):
    """A frame of the chair sequence (see chair.py), character bottom-centred at cx."""
    m = _manifest()
    cx = width / 2 if cx is None else cx
    strip, _ = _strip(anim, m["sit_yaw_index"])
    fw, fh = m["frame_size"]
    ground = m["ground_y"]
    scale, breathe = _setup(p, height, False, breath)

    chair = m["chair"]
    cw, ch = chair["frame_size"]
    dx, dy = chair["pull_offsets"][frame] if anim == "pull" else (0.0, 0.0)
    alpha = chair_alpha
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


@cache
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
