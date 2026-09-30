"""Quiet, deterministic expression timing; no timers or extra wakeups.

Blink spacing varies across the cycle so it doesn't look like a metronome. Transitions
start and finish at the unchanged neutral pose. Rendering remains on Buddy's existing
animation tick.
"""

CYCLE = 30.0
BLINKS = (2.0, 6.0, 10.4, 14.0, 18.7, 23.0, 27.0)
BLINK_DURATION = 0.42
GLANCE_START = 20.5
GLANCE_DURATION = 2.0


def blink_frame(seconds):
    phase = seconds % CYCLE
    for start in BLINKS:
        elapsed = phase - start
        if 0 <= elapsed < BLINK_DURATION:
            return (0, 1, 2, 2, 2, 1, 0)[min(6, int(elapsed / BLINK_DURATION * 7))]
    return 0


def idle_expression(seconds):
    phase = seconds % CYCLE
    if GLANCE_START <= phase < GLANCE_START + GLANCE_DURATION:
        return "glance", min(16, int((phase - GLANCE_START) / GLANCE_DURATION * 17))
    return "blink", blink_frame(seconds)
