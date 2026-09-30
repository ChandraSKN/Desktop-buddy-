import anthropic
import httpx2

from deskbuddy.services import corrector


def _out_of_credits():
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError("Your credit balance is too low to access the Anthropic API.",
                                     response=httpx2.Response(400, request=request), body=None)


def test_falls_back_to_the_cli_when_the_api_account_has_no_credits(monkeypatch):
    monkeypatch.setattr(corrector, "backend_name", lambda: "Claude API")
    monkeypatch.setattr(corrector, "_claude_cli", lambda: "/usr/bin/claude")

    def api(_, tone="Original"):
        raise _out_of_credits()

    monkeypatch.setattr(corrector, "correct_with_api", api)
    monkeypatch.setattr(corrector, "correct_with_cli", lambda t, tone="Original": (t.upper(), ["cli"]))
    assert corrector.correct("hi") == ("HI", ["cli"])


def test_other_api_errors_are_not_hidden(monkeypatch):
    monkeypatch.setattr(corrector, "backend_name", lambda: "Claude API")
    monkeypatch.setattr(corrector, "_claude_cli", lambda: "/usr/bin/claude")

    def api(_, tone="Original"):
        raise RuntimeError("boom")

    monkeypatch.setattr(corrector, "correct_with_api", api)
    try:
        corrector.correct("hi")
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected the error to propagate")


def test_tone_prompt_preserves_meaning_and_replaces_preserve_tone_instruction():
    for tone in corrector.TONES:
        prompt = corrector.prompt_for_tone(tone)
        if tone == "Original":
            assert prompt == corrector.SYSTEM_PROMPT
        else:
            assert f"Requested tone: {tone}" in prompt
            assert "meaning, tone, language" not in prompt
            assert "Do not add facts" in prompt


def test_tone_survives_api_fallback(monkeypatch):
    monkeypatch.setattr(corrector, "backend_name", lambda: "Claude API")
    monkeypatch.setattr(corrector, "_claude_cli", lambda: "claude")
    def api(text, tone):
        assert tone == "Friendly"
        raise _out_of_credits()
    monkeypatch.setattr(corrector, "correct_with_api", api)
    monkeypatch.setattr(corrector, "correct_with_cli", lambda text, tone: (tone, []))
    assert corrector.correct("hello", "Friendly") == ("Friendly", [])


def test_basic_backend_does_not_silently_ignore_tone(monkeypatch):
    import pytest
    monkeypatch.setattr(corrector, "backend_name", lambda: "LanguageTool (basic)")
    with pytest.raises(RuntimeError, match="Tone changes need Claude"):
        corrector.correct("hello", "Professional")


def test_panel_passes_selected_tone_to_worker(qtbot, monkeypatch):
    from deskbuddy.ui.corrector_panel import CorrectorPanel
    seen = []
    monkeypatch.setattr(corrector, "correct", lambda text, tone: (seen.append((text, tone)) or "Hi there!", []))
    panel = CorrectorPanel(None)
    qtbot.addWidget(panel)
    panel.input.setPlainText("hello")
    panel.tone.setCurrentText("Friendly")
    panel.run_fix()
    qtbot.waitUntil(lambda: panel.output.toPlainText() == "Hi there!", timeout=3000)
    panel.worker.wait()
    assert seen == [("hello", "Friendly")]
    assert panel.tone.isEnabled()
    # This isolated panel has no Buddy lifecycle to notify on teardown.
    panel.buddy = type("BuddyStub", (), {"panel_closed": lambda self: None, "say": lambda *args: None})()
