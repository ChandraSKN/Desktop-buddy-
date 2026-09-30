import numpy as np
import pytest

from deskbuddy.services.listener import (
    CONVERSATION,
    FRAME,
    RATE,
    Endpointer,
    WakeListener,
    find_wake,
    is_goodbye,
    is_record_command,
    is_stop_record_command,
    strip_wake,
)
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


def test_follow_up_after_an_answer_needs_no_wake_phrase():
    listener = WakeListener(quick=lambda a: pytest.fail("no wake check"), full=lambda a: "and tomorrow?")
    listener.expect_command(now=50, seconds=CONVERSATION)
    assert listener.on_utterance(None, now=55) == ("command", "and tomorrow?")


def test_a_cough_during_the_follow_up_window_keeps_it_open():
    said = iter(["", "what about friday"])
    listener = WakeListener(quick=lambda a: pytest.fail("no wake check"), full=lambda a: next(said))
    listener.expect_command(now=0, seconds=CONVERSATION)
    assert listener.on_utterance(None, now=2) is None
    assert listener.on_utterance(None, now=4) == ("command", "what about friday")


@pytest.mark.parametrize("text", ["Thanks.", "Thank you, buddy!", "That's all.", "That’s it, thanks",
                                  "No.", "Okay, bye", "no thanks"])
def test_thanks_ends_the_conversation(text):
    assert is_goodbye(text)


@pytest.mark.parametrize("text", ["", "no, what about friday", "stop the timer", "okay what about monday",
                                  "thanks, and open firefox"])
def test_questions_are_not_goodbyes(text):
    assert not is_goodbye(text)


def test_endpointer_recovers_when_the_room_is_suddenly_loud():
    """Mic gain reset to max after a reboot: the hiss never drops below the old gate."""
    rng = np.random.default_rng(2)
    hiss = [(rng.normal(0, 16000, FRAME)).clip(-32767, 32767).astype(np.int16) for _ in range(1200)]
    ep = Endpointer()
    run(ep, hiss)                                   # first block or two are the hiss itself
    assert ep.too_loud
    assert run(ep, hiss) == []                      # then it has learned the room


@pytest.mark.parametrize("phrase", ["Buddy, come back!", "Hey Buddy come back", "come back please"])
def test_return_command_is_local_even_without_hey(phrase):
    listener = WakeListener(lambda _: phrase, lambda _: pytest.fail("must not need full transcription"))
    assert listener.on_utterance(None, 100) == ("command", "come back")
    assert listener.on_utterance(None, 100, return_only=True) == ("command", "come back")


def test_background_listening_ignores_other_commands_and_pending_followups():
    listener = WakeListener(lambda _: "Hey Buddy open Firefox", lambda _: pytest.fail("must stay asleep"))
    listener.expect_command(100)
    assert listener.on_utterance(None, 101, return_only=True) is None
    assert listener.awaiting_until == 0


@pytest.mark.parametrize("phrase", ["Buddy let's talk", "Buddy, let’s talk!", "Hey Buddy let us talk"])
def test_talk_command_works_while_visible_or_dismissed(phrase):
    listener = WakeListener(lambda _: phrase, lambda _: pytest.fail("local command needs no full model"))
    assert listener.on_utterance(None, 100) == ("command", "let's talk")
    assert listener.on_utterance(None, 100, return_only=True) == ("command", "let's talk")


@pytest.mark.parametrize("phrase", ["record the meeting", "Record the meeting and write minutes of meeting.",
                                    "start recording", "please record this call", "take minutes",
                                    "can you record the meeting and write the minutes", "record my screen"])
def test_record_commands(phrase):
    assert is_record_command(phrase)


@pytest.mark.parametrize("phrase", ["what were the minutes of yesterday's meeting", "did you record the meeting",
                                    "don't record the meeting", "remind me to record the meeting at 3",
                                    "stop recording", "open the recording folder"])
def test_not_record_commands(phrase):
    assert not is_record_command(phrase)


def test_stop_recording_needs_buddy_while_the_meeting_is_being_recorded():
    assert is_stop_record_command("stop recording")
    assert is_stop_record_command("Hey Buddy, end the recording please", need_name=True)
    assert not is_stop_record_command("stop recording", need_name=True)
    assert not is_stop_record_command("let's stop the discussion here", need_name=True)


def test_while_recording_only_buddy_stop_recording_is_heard():
    said = iter(["Hey Buddy open Firefox", "stop recording", "Buddy, stop recording."])
    listener = WakeListener(lambda _: next(said), lambda _: pytest.fail("the meeting isn't transcribed"))
    listener.expect_command(100)
    assert listener.on_utterance(None, 101, stop_only=True) is None
    assert listener.on_utterance(None, 102, stop_only=True) is None
    assert listener.on_utterance(None, 103, stop_only=True) == ("command", "stop recording")
