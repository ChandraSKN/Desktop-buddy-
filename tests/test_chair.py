import pytest

from deskbuddy.character import chair, model3d

pytestmark = pytest.mark.skipif(not chair.available(), reason="sitting frames not rendered")


def test_frames_play_forward_then_backward():
    assert chair.phase_frame("pull", 0) == 0
    assert chair.phase_frame("pull", 99) == 27
    assert chair.phase_frame("push", 0) == 27
    assert chair.phase_frame("push", 99) == 0
    assert chair.phase_length("seated") == float("inf")


def test_reversing_midway_keeps_the_same_frame():
    for frame in range(28):
        t = chair.phase_time_for_frame("push", frame)
        assert chair.phase_frame("push", t + 1e-6) == frame


def test_chair_offset_for_every_pull_frame():
    m = model3d._manifest()
    assert len(m["chair"]["pull_offsets"]) == m["anims"]["pull"]["2"]["frames"]
    assert m["chair"]["pull_offsets"][-1] == [0.0, 0.0]


def test_scene_runs_through_to_seated_and_stays():
    scene = chair.ChairScene(0.0)
    assert scene.frame(0.0)[2] == 0.0                       # chair starts invisible
    assert scene.advance(10.0) and scene.phase == "seated"
    assert scene.advance(10_000.0) and scene.phase == "seated"


def test_standing_up_ends_with_the_chair_gone():
    scene = chair.ChairScene(0.0)
    scene.advance(10.0)
    scene.stand_up(10.0)
    assert scene.leaving and scene.phase == "stand"
    assert scene.frame(10.0)[:2] == ("sit", 13)               # starts from fully seated
    assert not scene.advance(20.0)


def test_standing_up_while_the_chair_fades_in_fades_it_out():
    scene = chair.ChairScene(0.0)
    scene.stand_up(0.1)
    assert scene.phase == "fade_out"
    assert scene.frame(0.1)[2] == pytest.approx(0.25)         # same opacity, now fading out


def test_interrupting_the_pull_pushes_back_from_the_same_frame():
    scene = chair.ChairScene(0.0)
    scene.advance(1.0)                                        # 0.6 s into the pull
    before = scene.frame(1.0)[1]
    scene.stand_up(1.0)
    assert scene.phase == "push" and scene.frame(1.0)[1] == before
