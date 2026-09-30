"""Expressions return to neutral and cannot replace an active gesture."""

import pytest

from deskbuddy.character import expression, model3d


def test_blink_is_brief_and_returns_to_neutral():
    start = expression.BLINKS[0]
    assert expression.blink_frame(start - 0.01) == 0
    assert expression.blink_frame(start + 0.07) == 1
    assert expression.blink_frame(start + 0.14) == 2
    assert expression.blink_frame(start + 0.33) == 1
    assert expression.blink_frame(start + 0.43) == 0
    assert expression.blink_frame(start + expression.CYCLE + 0.14) == 2


def test_glance_eases_between_neutral_endpoints():
    start = expression.GLANCE_START
    assert expression.idle_expression(start) == ("glance", 0)
    assert expression.idle_expression(start + 1) == ("glance", 8)
    assert expression.idle_expression(start + 1.99) == ("glance", 16)
    assert expression.idle_expression(start + 2) == ("blink", 0)


@pytest.mark.parametrize("walking,mouth,wave_t,activity,expected", [
    (False, None, None, None, "blink"),
    (True, None, None, None, "walk"),
    (False, 2, None, None, "talk"),
    (False, None, 0.1, None, "wave"),
    (False, None, None, "film", "film"),
])
def test_expression_never_overrides_existing_actions(qapp, monkeypatch, walking, mouth, wave_t, activity, expected):
    from PyQt6.QtGui import QImage, QPainter
    if not model3d.can_do("blink"):
        pytest.skip("expressions not rendered")
    actual = model3d._strip
    drawn = []

    def strip(anim, yi):
        drawn.append(anim)
        return actual(anim, yi)

    monkeypatch.setattr(model3d, "_strip", strip)
    img = QImage(200, 300, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(img)
    try:
        model3d.draw_model(painter, 200, 300, 0, walking, 0, -0.22, wave_t=wave_t,
                           mouth=mouth, activity=activity, activity_t=2.18)
    finally:
        painter.end()
    assert drawn[-1] == expected
