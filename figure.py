"""Illustrated character built from sprite.png: the original artwork for the body, plus
drawn shins and shoes so the legs can walk, and a detachable hand that can wave.

All coordinates below are in sprite.png pixels (424x640). The artwork stops at the
knees, so each leg is: the artwork's thigh + a drawn trouser shin + a dress shoe."""

import math
from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QImage, QPainter, QPainterPath,
                         QPen, QPolygonF, QRadialGradient, QTransform)

SPRITE = Path(__file__).resolve().parent / "sprite.png"

FIG_H = 915            # full figure height in sprite px (head to shoe soles)
CROTCH_Y = 548         # where the two trouser legs separate in the artwork
KNEE_Y = 640           # bottom edge of the artwork
HEM_Y = 872            # trouser hem
SPLIT_X = 152          # x that separates the left leg from the right one
HIP_Y = 536            # legs rotate around this height
WRIST = QPointF(352, 250)   # the waving hand rotates around this point
HAND_CUT_Y = 244            # pixels above this line (and right of HAND_CUT_X) are the hand
HAND_CUT_X = 318

TROUSER = QColor(21, 25, 28)
OUTLINE = QColor(7, 8, 9)
SHOE = QColor(18, 18, 20)
SHOE_SHINE = QColor(92, 98, 110)

# (hip x, left edge at knee, right edge at knee, left edge at hem, right edge at hem)
LEGS = {
    "left": (107, 61, 133, 67, 129),
    "right": (196, 155, 228, 160, 223),
}


@lru_cache(maxsize=1)
def _sprite():
    return QImage(str(SPRITE)).convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)


def _blank(w, h):
    img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    return img


@lru_cache(maxsize=1)
def _body():
    """Artwork above the crotch, with the waving hand removed (it's drawn separately)."""
    src = _sprite()
    img = _blank(src.width(), CROTCH_Y + 6)
    p = QPainter(img)
    p.drawImage(0, 0, src, 0, 0, src.width(), CROTCH_Y + 6)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    p.fillRect(QRectF(HAND_CUT_X, 0, src.width(), HAND_CUT_Y), Qt.GlobalColor.transparent)
    p.end()
    return img


@lru_cache(maxsize=1)
def _hand():
    src = _sprite()
    img = _blank(src.width(), HAND_CUT_Y + 2)
    p = QPainter(img)
    p.drawImage(HAND_CUT_X, 0, src, HAND_CUT_X, 0, src.width() - HAND_CUT_X, HAND_CUT_Y + 2)
    p.end()
    return img


@lru_cache(maxsize=2)
def _leg(side):
    """One full leg, top at HIP_Y - 12, in figure coordinates (so it can be drawn at 0,0)."""
    hip_x, kl, kr, hl, hr = LEGS[side]
    src = _sprite()
    img = _blank(src.width(), FIG_H)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Thigh straight from the artwork.
    x0, x1 = (0, SPLIT_X) if side == "left" else (SPLIT_X, src.width())
    top = HIP_Y - 12
    p.drawImage(x0, top, src, x0, top, x1 - x0, KNEE_Y - top)

    # Shin: stretch the artwork's last rows down to the hem, so the shading, fabric
    # highlights and outline continue seamlessly from the drawn trousers.
    strip = src.copy(kl - 3, KNEE_Y - 8, kr - kl + 6, 8)
    shin = QPolygonF([QPointF(kl - 3, KNEE_Y - 8), QPointF(kr + 3, KNEE_Y - 8),
                      QPointF(hr + 3, HEM_Y), QPointF(hl - 3, HEM_Y)])
    mapping = QTransform()
    if QTransform.quadToQuad(QPolygonF(QRectF(0, 0, strip.width(), strip.height())), shin, mapping):
        p.save()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setTransform(mapping, True)
        p.drawImage(0, 0, strip)
        p.restore()
    # trouser crease, like the ones drawn in the artwork
    mid_k, mid_h = (kl + kr) / 2, (hl + hr) / 2
    p.setPen(QPen(QColor(58, 62, 66, 120), 1.5))
    p.drawLine(QPointF(mid_k + 4, KNEE_Y + 10), QPointF(mid_h + 3, HEM_Y - 16))

    # Dress shoe peeking out below the hem.
    cx = (hl + hr) / 2
    shoe = QPainterPath()
    shoe.moveTo(hl - 6, HEM_Y - 6)
    shoe.lineTo(hr + 6, HEM_Y - 6)
    shoe.cubicTo(hr + 16, HEM_Y + 8, hr + 14, HEM_Y + 38, cx + 18, FIG_H - 4)
    shoe.lineTo(cx - 18, FIG_H - 4)
    shoe.cubicTo(hl - 14, HEM_Y + 38, hl - 16, HEM_Y + 8, hl - 6, HEM_Y - 6)
    p.setPen(QPen(OUTLINE, 3.2))
    p.setBrush(SHOE)
    p.drawPath(shoe)
    shine = QRadialGradient(QPointF(cx - 6, HEM_Y + 14), 22)
    shine.setColorAt(0, SHOE_SHINE)
    shine.setColorAt(1, QColor(SHOE_SHINE.red(), SHOE_SHINE.green(), SHOE_SHINE.blue(), 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(shine)
    p.drawEllipse(QPointF(cx - 6, HEM_Y + 14), 20, 9)
    # trouser hem over the top of the shoe
    p.setPen(QPen(OUTLINE, 3.2, cap=Qt.PenCapStyle.RoundCap))
    p.setBrush(TROUSER)
    hem = QPainterPath()
    hem.moveTo(hl - 3, HEM_Y - 12)
    hem.lineTo(hr + 3, HEM_Y - 12)
    hem.quadTo(hr + 5, HEM_Y + 2, cx, HEM_Y + 4)
    hem.quadTo(hl - 5, HEM_Y + 2, hl - 3, HEM_Y - 12)
    p.drawPath(hem)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRect(QRectF(hl - 1, HEM_Y - 16, hr - hl + 2, 8))   # hide the hem's top stroke
    p.end()
    return img, QPointF(hip_x, HIP_Y)


def figure_size():
    return _sprite().width(), FIG_H


def draw_figure(p, width, height, phase, walking, breath, flip, wave_t=None):
    """Draw the character bottom-centred in a width x height area.
    phase: walk cycle angle; flip: +1 facing right .. -1 facing left (animated);
    wave_t: seconds into a wave, or None."""
    fw, fh = figure_size()
    scale = (height - 14) / fh
    stride = math.sin(phase) if walking else 0.0
    bob = abs(math.sin(phase)) * 5 if walking else 0.0   # upper body only; planted foot stays down
    tilt = stride * 1.6
    breathe = 1 + math.sin(breath) * 0.006

    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    # soft ground shadow (shrinks a little at the top of each bounce)
    sh_w = fw * scale * (0.62 - bob * 0.004)
    shadow = QRadialGradient(QPointF(width / 2, height - 8), sh_w / 2)
    shadow.setColorAt(0, QColor(10, 16, 30, 90))
    shadow.setColorAt(1, QColor(10, 16, 30, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(shadow)
    p.drawEllipse(QRectF(width / 2 - sh_w / 2, height - 15, sh_w, 14))

    # figure space: origin at the shoe soles, centred
    p.translate(width / 2, height - 6)
    p.scale(scale, scale)
    p.rotate(tilt * (1 if flip >= 0 else -1))
    p.scale(flip if abs(flip) > 0.06 else 0.06, 1)
    p.translate(-fw / 2, -fh)

    # legs: small swing, and the stepping leg lifts its foot. The planted leg is drawn first.
    swing = 5.5 * stride
    order = ["left", "right"] if stride >= 0 else ["right", "left"]
    for side in order:
        img, hip = _leg(side)
        forward = stride if side == "right" else -stride
        lift = max(0.0, forward) * 26 if walking else 0.0
        p.save()
        p.translate(hip)
        p.rotate(swing)
        p.translate(-hip.x(), -hip.y() - lift)
        p.drawImage(0, 0, img)
        p.restore()

    # upper body, bobbing with each step and breathing from the hips
    p.save()
    p.translate(0, -bob)
    p.translate(fw / 2, CROTCH_Y)
    p.scale(1, breathe)
    p.translate(-fw / 2, -CROTCH_Y)
    p.drawImage(0, 0, _body())
    hand_angle = 0.0
    if wave_t is not None:
        hand_angle = math.sin(wave_t * 9) * 13 * min(1.0, wave_t * 3)
    p.translate(WRIST)
    p.rotate(hand_angle)
    p.translate(-WRIST)
    p.drawImage(0, 0, _hand())
    p.restore()
    p.restore()
