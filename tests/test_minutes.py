import json
from datetime import UTC, datetime, timedelta

from deskbuddy.services import minutes as minutes_mod
from deskbuddy.services.memory import MemoryStore
from deskbuddy.services.minutes import ActionItem, Minutes
from deskbuddy.services.transcribe import as_text, is_echo, merge

NOW = datetime(2026, 9, 27, 15, 0, tzinfo=UTC)


def seg(start, end, speaker, text):
    return {"start": start, "end": end, "speaker": speaker, "text": text}


def test_merge_orders_by_time_and_drops_mic_echo():
    others = [seg(0, 4, "Others", "Can you send the report by Friday?"),
              seg(10, 12, "Others", "Great, thanks.")]
    you = [seg(0.3, 4.1, "You", "can you send the report by friday"),     # heard through speakers
           seg(5, 8, "You", "Sure, I'll send it Thursday evening.")]
    merged = merge({"You": you, "Others": others})
    assert [s["text"] for s in merged] == ["Can you send the report by Friday?",
                                           "Sure, I'll send it Thursday evening.", "Great, thanks."]


def test_real_overlapping_talk_is_not_echo():
    others = [seg(0, 4, "Others", "The budget is approved for next quarter.")]
    assert not is_echo(seg(1, 3, "You", "Wait, which project?"), others)


def test_transcript_text_has_timestamps_and_speakers():
    assert as_text([seg(3725, 3730, "You", " Hello ")]) == "[1:02:05] You: Hello"


def test_render_puts_action_items_after_the_summary():
    m = Minutes(summary="We agreed the launch plan.",
                action_items=[ActionItem(owner="You", task="Send the report", due="Friday"),
                              ActionItem(owner="Ravi", task="Book the room", due="")],
                decisions=["Launch on the 5th"], discussion=["Timeline"], open_questions=[])
    meta = {"title": "Launch sync", "recorded_from": NOW.isoformat(),
            "recorded_to": (NOW + timedelta(minutes=30)).isoformat(),
            "start": NOW.isoformat(), "end": (NOW + timedelta(minutes=30)).isoformat()}
    md = minutes_mod.render(m, meta, [seg(0, 2, "You", "hi")])
    assert md.startswith("# Minutes: Launch sync")
    assert md.index("## Summary") < md.index("## Action items") < md.index("## Decisions")
    assert "- [ ] **You**: Send the report _(due Friday)_" in md
    assert "- [ ] **Ravi**: Book the room\n" in md
    assert "Open questions" not in md                              # empty sections are left out
    assert "[0:00:00] You: hi" in md


def test_write_minutes_request(monkeypatch):
    seen = {}

    class FakeBeta:
        def parse(self, **kw):
            seen.update(kw)
            from types import SimpleNamespace
            return SimpleNamespace(stop_reason="end_turn", parsed_output=Minutes(
                summary="s", action_items=[], decisions=[], discussion=[], open_questions=[]))

    from types import SimpleNamespace
    client = SimpleNamespace(beta=SimpleNamespace(messages=FakeBeta()))
    minutes_mod.write_minutes([seg(0, 2, "Others", "hello")], "Sync", ["Likes action items first"], client)
    assert seen["model"] == "claude-opus-5" and seen["output_format"] is Minutes
    assert seen["fallbacks"] == "default"
    assert "Likes action items first" in seen["system"]
    assert "Others: hello" in seen["messages"][0]["content"]


def test_minutes_are_searchable_and_fall_back_to_recent():
    store = MemoryStore(":memory:")
    store.add_minutes("Uday co-axial project", NOW, "/x/a.md", "Decided to use RG-6 cable.")
    store.add_minutes("Standup", NOW + timedelta(days=1), "/x/b.md", "Nothing new.")
    assert [m.title for m in store.search_minutes("what cable did we pick for uday?")] == \
        ["Uday co-axial project"]
    assert store.search_minutes("?")[0].title == "Standup"           # newest first


def test_minutes_saved_next_to_a_marker(tmp_path):
    folder = tmp_path / "2026-09-27 2030 Sync"
    folder.mkdir()
    path = minutes_mod.save("# m", folder, {}, tmp_path / "out")
    assert path.read_text() == "# m"
    assert json.loads((folder / "minutes.json").read_text())["path"] == str(path)


def test_hallucinated_and_silent_segments_are_dropped():
    from types import SimpleNamespace as NS

    from deskbuddy.services.transcribe import looks_real
    real = NS(text="Ravi will fix the bugs.", compression_ratio=1.6, no_speech_prob=0.04, avg_logprob=-0.2)
    loop = NS(text="ლ ლ ლ ლ ლ ლ", compression_ratio=14.9, no_speech_prob=0.67, avg_logprob=-0.08)
    unsure = NS(text="mm", compression_ratio=1.0, no_speech_prob=0.1, avg_logprob=-1.4)
    assert looks_real(real) and not looks_real(loop) and not looks_real(unsure)
