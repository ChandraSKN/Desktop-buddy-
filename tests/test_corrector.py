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

    def api(_):
        raise _out_of_credits()

    monkeypatch.setattr(corrector, "correct_with_api", api)
    monkeypatch.setattr(corrector, "correct_with_cli", lambda t: (t.upper(), ["cli"]))
    assert corrector.correct("hi") == ("HI", ["cli"])


def test_other_api_errors_are_not_hidden(monkeypatch):
    monkeypatch.setattr(corrector, "backend_name", lambda: "Claude API")
    monkeypatch.setattr(corrector, "_claude_cli", lambda: "/usr/bin/claude")

    def api(_):
        raise RuntimeError("boom")

    monkeypatch.setattr(corrector, "correct_with_api", api)
    try:
        corrector.correct("hi")
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected the error to propagate")
