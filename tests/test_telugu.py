import pytest

from deskbuddy.services.launcher import open_request
from deskbuddy.services.telugu import has_telugu, sound_key, to_latin


@pytest.mark.parametrize("telugu, english", [
    ("ఫైర్‌ఫాక్స్", "Firefox"), ("కాలిక్యులేటర్", "Calculator"), ("క్రోమ్", "Chrome"),
    ("టెర్మినల్", "Terminal"), ("సెట్టింగ్స్", "Settings"), ("బ్లెండర్", "Blender"),
    ("యూట్యూబ్", "YouTube"), ("డౌన్‌లోడ్స్", "Downloads"), ("ఫైల్స్", "Files"),
])
def test_english_names_written_in_telugu_sound_the_same(telugu, english):
    assert sound_key(telugu) == sound_key(english)


def test_different_names_do_not_collide():
    assert sound_key("కాలిక్యులేటర్") != sound_key("Calendar")
    assert sound_key("ఫైల్స్") != sound_key("Firefox")


def test_transliteration_basics():
    assert to_latin("ఫైర్‌ఫాక్స్") == "phairphaaks"
    assert to_latin("సెట్టింగ్స్") == "settings"                 # nasal before g is n
    assert has_telugu("నా మీటింగ్") and not has_telugu("my meeting")


@pytest.mark.parametrize("said, what", [
    ("firefox open cheyyi", "firefox"), ("Calculator open chey.", "Calculator"),
    ("Hey buddy, VS Code open cheyandi", "VS Code"), ("downloads teruvu", "downloads"),
    ("youtube kholo", "youtube"), ("ఫైర్‌ఫాక్స్ ఓపెన్ చెయ్యి", "ఫైర్‌ఫాక్స్"),
    ("కాలిక్యులేటర్ తెరువు", "కాలిక్యులేటర్"),
])
def test_telugu_and_hindi_word_order(said, what):
    assert open_request(said) == what


@pytest.mark.parametrize("said", ["meeting eppudu undi", "ఈ రోజు నా మీటింగ్స్ ఏంటి",
                                  "is it open cheyyi time"])
def test_telugu_questions_are_not_open_requests(said):
    assert open_request(said) is None


def test_telugu_names_open_the_right_things(tmp_path):
    from deskbuddy.services import launcher as L
    apps = tmp_path / "applications"
    apps.mkdir()
    (apps / "firefox.desktop").write_text("[Desktop Entry]\nType=Application\nName=Firefox\nExec=firefox")
    ran = []
    fake = L.Launcher(dirs=[tmp_path], spawn=lambda argv, label: ran.append(argv) or True)
    assert fake.open("ఫైర్‌ఫాక్స్") == (True, "Opening Firefox.")
    assert fake.open("ఫోటోషాప్") == (False, None)                 # not installed: left for Claude


def test_telugu_replies_use_the_telugu_voice(qtbot):
    from deskbuddy.ui.voice import Speaker
    sp = Speaker()
    if not sp.telugu.available():
        pytest.skip("Telugu voice not downloaded")
    assert sp.voice_for("మీ తర్వాతి మీటింగ్ రేపు") is sp.telugu
    assert sp.voice_for("Your next meeting is tomorrow") is sp.voice


def test_minutes_keep_romanized_telugu_and_translate_other_scripts():
    from types import SimpleNamespace as NS

    from deskbuddy.services.transcribe import is_loop, mostly_latin
    assert mostly_latin("Ravi export bugs Budawaram lopu fix chesthadu.")
    assert not mostly_latin("ना तरवाती मीटिं एपडू")                    # Telugu sounds in Devanagari
    assert not mostly_latin("మీ తర్వాతి మీటింగ్")
    assert is_loop(NS(compression_ratio=14.0, no_speech_prob=0.1))
    assert not is_loop(NS(compression_ratio=14.0, no_speech_prob=0.9))  # silence, not a loop


def test_the_assistant_is_told_the_language_and_to_reply_in_telugu():
    from types import SimpleNamespace as NS

    from deskbuddy.services.agent import Agent
    from deskbuddy.services.agent_tools import ToolBox
    from deskbuddy.services.memory import MemoryStore
    seen = []

    class Stream:
        def __init__(self, kw):
            seen.append(kw)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter([])

        def get_final_message(self):
            return NS(stop_reason="end_turn", content=[NS(type="text", text="సరే")])

    client = NS(beta=NS(messages=NS(stream=lambda **kw: Stream(kw))))
    agent = Agent(ToolBox(MemoryStore(":memory:"), lambda: []), client=client)
    agent.send("When is my next meeting?", spoken=True, language="te")
    context = seen[0]["messages"][0]["content"][0]["text"]
    assert "They spoke Telugu" in context and "Telugu script" in context
    agent.send("thanks", spoken=True, language="en")
    second_question = seen[1]["messages"][2]                 # user, assistant, user
    assert "They spoke" not in second_question["content"][0]["text"]


def test_telugu_speech_reaches_the_assistant_with_its_language(qtbot, monkeypatch):
    from deskbuddy.services import agent as agent_mod
    from deskbuddy.ui.buddy_window import Buddy
    monkeypatch.setattr(Buddy, "sync_calendar", lambda self: None)
    b = Buddy()
    qtbot.addWidget(b)
    b.calendar_timer.stop()
    b.reminder_timer.stop()
    got = []

    class FakeAgent:
        def send(self, text, on_text, on_action, spoken=False, language="en"):
            got.append((text, language))
            return "మీ తర్వాతి మీటింగ్ రేపు"

    monkeypatch.setattr(agent_mod, "available", lambda: True)
    monkeypatch.setattr(b.speaker, "say", lambda text: True)
    b.chat.make_agent = FakeAgent
    b.on_heard("When is my next meeting?", "te")
    qtbot.waitUntil(lambda: bool(got), timeout=3000)
    assert got == [("When is my next meeting?", "te")]
