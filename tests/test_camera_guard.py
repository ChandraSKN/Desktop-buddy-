from unittest.mock import Mock

from deskbuddy.services.camera_guard import GuardPolicy
from deskbuddy.ui.camera_guard import CameraGuard


def challenge():
    guard = GuardPolicy()
    for now in (0, 1, 2):
        guard.observe("unknown", now)
    assert guard.state == "challenge"
    return guard


def test_only_unknown_faces_trigger():
    guard = GuardPolicy()
    for i in range(10):
        guard.observe("empty", i)
    assert guard.state == "watching"
    guard.observe("unknown", 11)
    guard.observe("owner", 12)
    guard.observe("unknown", 13)
    assert guard.state == "watching"


def test_phrase_exact_normalized_and_allowed_session_expires():
    guard = challenge()
    assert not guard.answer("not kamal is great")
    assert not guard.answer("kamal is greater")
    assert guard.answer("Kamal is great!")
    guard.observe("unknown", 4)
    assert guard.state == "allowed"
    guard.observe("empty", 5)
    guard.observe("empty", 10)
    assert guard.state == "watching"


def test_shutdown_requires_fresh_camera_and_two_deadlines():
    guard = challenge()
    guard.observe("unknown", 22)
    assert guard.tick(22) == "countdown"
    guard.observe("unknown", 51)
    assert guard.tick(51) == "countdown"
    guard.observe("unknown", 52)
    assert guard.tick(52) == "poweroff"
    assert guard.tick(52) != "poweroff"


def test_stale_camera_cancels_shutdown():
    guard = challenge()
    assert guard.tick(22) == "stale"
    assert guard.deadline is None


def test_owner_or_empty_cancels_both_phases():
    for presence in ("owner", "empty"):
        for countdown in (False, True):
            guard = challenge()
            if countdown:
                guard.observe("unknown", 22)
                guard.tick(22)
            guard.observe(presence, 23)
            assert guard.state == "watching"
            assert guard.deadline is None


def test_phrase_during_countdown_cancels():
    guard = challenge()
    guard.observe("unknown", 22)
    guard.tick(22)
    assert guard.answer("kamal is great")
    guard.observe("unknown", 52)
    assert guard.tick(52) == "allowed"


def test_controller_routes_answers_and_cancel(qtbot):
    from PyQt6.QtWidgets import QWidget
    buddy = QWidget()
    qtbot.addWidget(buddy)
    buddy.voice_reply = Mock()
    guard = CameraGuard(buddy)
    guard.active = True
    guard.policy = challenge()
    assert guard.answer("kamal is great")
    buddy.voice_reply.assert_called_once_with("Welcome. Shutdown cancelled.")
    guard.stop()
    assert not guard.active
    assert not guard.answer("kamal is great")
    assert not guard.timer.isActive()


def test_controller_never_powers_off_on_mic_failure(qtbot):
    from PyQt6.QtWidgets import QWidget
    buddy = QWidget()
    qtbot.addWidget(buddy)
    buddy.listen = False
    buddy.say = Mock()
    guard = CameraGuard(buddy)
    guard.active = True
    guard.power = Mock()
    guard.policy = challenge()
    guard.tick()
    assert not guard.active
    guard.power.start.assert_not_called()


def test_controller_poweroff_is_normal_noninteractive_and_once(qtbot, monkeypatch):
    from PyQt6.QtWidgets import QWidget
    buddy = QWidget()
    qtbot.addWidget(buddy)
    buddy.listen = True
    buddy.listener = Mock()
    buddy.listener.isRunning.return_value = True
    buddy.on_call = Mock(return_value=False)
    buddy.voice_state = "listening"
    guard = CameraGuard(buddy)
    guard.active = True
    guard.power = Mock()
    guard.policy = challenge()
    guard.policy.observe("unknown", 22)
    guard.policy.tick(22)
    guard.policy.observe("unknown", 52)
    monkeypatch.setattr("deskbuddy.ui.camera_guard.time.monotonic", lambda: 52)
    guard.tick()
    guard.power.start.assert_called_once_with("systemctl", ["--no-ask-password", "poweroff"])
    assert not guard.active


def test_camera_failure_releases_device(monkeypatch):
    import sys

    from deskbuddy.ui.camera_guard import CameraThread
    camera = Mock()
    camera.isOpened.return_value = True
    camera.read.return_value = (False, None)
    cv2 = Mock()
    cv2.VideoCapture.return_value = camera
    monkeypatch.setitem(sys.modules, "cv2", cv2)
    monkeypatch.setitem(sys.modules, "face_recognition", Mock())
    worker = CameraThread()
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures == ["The camera stopped responding."]
    camera.release.assert_called_once()
