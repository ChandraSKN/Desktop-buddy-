import numpy as np
import pytest

from deskbuddy.services.listener import FRAME, RATE, Endpointer, WakeListener, find_wake, strip_wake
from deskbuddy.services.speech import Voice, clean_for_speech, mouth_levels


@pytest.mark.parametrize("heard, command", [
    ("Hey Buddy, what's my next meeting?", "what's my next meeting?"),
    (" Hey, buddy! Remind me at 5.", "Remind me at 5."),
    ("Heybuddy what time is it", "what time is it"),
    ("OK Buddy set a reminder", "set a reminder"),
    ("Hey Buddy.", ""),
])
def test_wake_phrase_variants(heard, command):
    assert find_wake(heard) is not None
    assert strip_wake(heard) == command


@pytest.mark.parametrize("heard", ["My buddy said hey buddy", "I told my buddy", "Hey, how are you?",
                                   "Let's ask somebody", ""])
def test_ordinary_speech_does_not_wake_him(heard):
    assert find_wake(heard) is None


def frames(seconds, amplitude, freq=220):
    t = np.arange(int(seconds * RATE)) / RATE
    wave = (np.sin(2 * np.pi * freq * t) * amplitude * 32767).astype(np.int16)
    return [wave[i:i + FRAME] for i in range(0, len(wave) - FRAME + 1, FRAME)]


def run(endpointer, chunks):
    return [u for u in (endpointer.feed(c) for c in chunks) if u is not None]


def test_endpointer_finds_an_utterance_between_silences():
    rng = np.random.default_rng(0)
    quiet = [(rng.normal(0, 30, FRAME)).astype(np.int16) for _ in range(60)]
    got = run(Endpointer(), quiet + frames(1.5, 0.3) + quiet)
    assert len(got) == 1
    assert 1.5 <= len(got[0]) / RATE <= 1.5 + 0.3 + 0.75 + 0.1     # speech + pre-roll + tail


def test_endpointer_ignores_clicks_and_follows_a_noisy_room():
    rng = np.random.default_rng(1)
    fan = [(rng.normal(0, 300, FRAME)).astype(np.int16) for _ in range(200)]     # steady noise
    click = frames(0.06, 0.5)
    assert run(Endpointer(), fan + click + fan) == []


def test_bare_wake_then_command_within_a_few_seconds():
    said = iter(["Hey Buddy.", "what's my next meeting"])
    listener = WakeListener(quick=lambda a: next(said), full=lambda a: next(said))
    assert listener.on_utterance(None, now=100.0) == ("wake",)
    assert listener.on_utterance(None, now=103.0) == ("command", "what's my next meeting")


def test_command_too_late_is_ignored():
    listener = WakeListener(quick=lambda a: "Hey Buddy", full=lambda a: "too late")
    listener.on_utterance(None, now=100.0)
    listener.quick = lambda a: "something else entirely"
    assert listener.on_utterance(None, now=120.0) is None


def test_wake_and_command_in_one_breath_uses_the_multilingual_model():
    listener = WakeListener(quick=lambda a: "hey buddy remind me to call amma",
                            full=lambda a: "Hey Buddy, remind me to call Amma")
    assert listener.on_utterance(None, now=0) == ("command", "remind me to call Amma")


def test_push_to_talk_skips_the_wake_phrase():
    listener = WakeListener(quick=lambda a: pytest.fail("no wake check"), full=lambda a: "what's next")
    listener.expect_command(now=0)
    assert listener.on_utterance(None, now=1) == ("command", "what's next")


def test_speech_text_has_no_markdown_links_or_emoji():
    said = clean_for_speech("**Done!** 📅 Join at https://teams.microsoft.com/x\n- 15:30–16:00")
    assert said == "Done! Join at the link 15:30 to 16:00"


def test_mouth_follows_loudness_and_does_not_flicker_shut():
    rate = 16000
    loud = np.sin(np.arange(rate // 2) / 5) * 20000
    gap = np.zeros(rate // 25)                          # one 40 ms frame of silence mid-word
    levels = mouth_levels(np.concatenate([loud, gap, loud, np.zeros(rate // 2)]).astype(np.int16), rate)
    assert max(levels) == 3 and levels[-1] == 0
    assert levels[len(loud) * 25 // rate] > 0            # the one-frame gap doesn't close it
    assert len(levels) == pytest.approx((len(loud) * 2 + len(gap) + rate / 2) / rate * 25, abs=1)


@pytest.mark.skipif(not Voice().available(), reason="Piper voice not downloaded")
def test_piper_speaks_to_a_wav(tmp_path):
    levels, seconds = Voice().synthesize("Your next meeting is at three.", tmp_path / "x.wav")
    assert 1.0 < seconds < 5.0 and (tmp_path / "x.wav").stat().st_size > 20000
    assert len(levels) == pytest.approx(seconds * 25, abs=1) and max(levels) >= 2
