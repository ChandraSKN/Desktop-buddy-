from PyQt6.QtCore import QTimer


def after(owner, ms, fn):
    """Call fn once after ms, unless owner is destroyed first (the timer is its child)."""
    timer = QTimer(owner, singleShot=True)
    timer.timeout.connect(fn)
    timer.timeout.connect(timer.deleteLater)
    timer.start(ms)
    return timer
