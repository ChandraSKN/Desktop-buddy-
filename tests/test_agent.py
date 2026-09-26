"""The agent loop against a fake Claude client (no network, no API key)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS

from deskbuddy.services.agent import Agent
from deskbuddy.services.agent_tools import ToolBox
from deskbuddy.services.memory import MemoryStore
from deskbuddy.services.reminders import Event

NOW = datetime(2026, 9, 26, 10, 0, tzinfo=UTC)
MEETING = Event("m1", NOW + timedelta(hours=1), "Design review", NOW + timedelta(hours=2),
                "https://meet.google.com/abc")


def text(t):
    return NS(type="text", text=t)


def tool(id_, name, input_):
    return NS(type="tool_use", id=id_, name=name, input=input_)


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        for block in self.message.content:
            if block.type == "text":
                yield NS(type="text", text=block.text)

    def get_final_message(self):
        return self.message


class FakeClient:
    """Replays scripted responses and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.beta = NS(messages=NS(stream=self._stream))

    def _stream(self, **params):
        self.requests.append({**params, "messages": list(params["messages"])})
        return FakeStream(self.responses.pop(0))


def make(client):
    store = MemoryStore(":memory:")
    box = ToolBox(store, lambda: [MEETING], now=lambda: NOW)
    return Agent(box, client=client, now=lambda: NOW), store


def test_tool_loop_runs_the_tool_and_returns_the_final_answer():
    client = FakeClient(
        NS(stop_reason="tool_use", content=[text("Let me set that. "),
                                            tool("t1", "create_reminder",
                                                 {"text": "Email Ravi", "after_meeting": "m1"})]),
        NS(stop_reason="end_turn", content=[text("Done, I'll remind you after the design review.")]),
    )
    agent, store = make(client)
    streamed = []
    reply = agent.send("remind me after the design review to email Ravi", streamed.append)

    assert "remind you after the design review" in reply
    assert "".join(streamed).startswith("Let me set that.")
    assert store.pending_reminders()[0].due_at == MEETING.end
    second = client.requests[1]["messages"]
    assert second[-1]["content"][0]["tool_use_id"] == "t1"
    assert second[-1]["content"][0]["is_error"] is False


def test_request_shape_model_caching_thinking_and_fallbacks():
    client = FakeClient(NS(stop_reason="end_turn", content=[text("Hi!")]))
    agent, _ = make(client)
    agent.send("hello")
    req = client.requests[0]
    assert req["model"] == "claude-opus-5"
    assert req["thinking"] == {"type": "adaptive"}
    assert req["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert req["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in req["betas"]
    assert all(t["eager_input_streaming"] for t in req["tools"])


def test_volatile_context_goes_in_the_user_turn_not_the_system_prompt():
    client = FakeClient(NS(stop_reason="end_turn", content=[text("a")]),
                        NS(stop_reason="end_turn", content=[text("b")]))
    agent, store = make(client)
    store.remember("Prefers morning meetings", NOW)
    agent.send("when do I like meetings?")
    agent.send("thanks")
    first, second = client.requests
    assert first["system"] == second["system"]               # cacheable prefix is stable
    context = first["messages"][0]["content"][0]["text"]
    assert "Design review" in context and "Prefers morning meetings" in context
    # history is append-only: the second request starts with the first one's messages
    assert second["messages"][:len(first["messages"])] == first["messages"]


def test_refusal_stops_without_running_tools():
    client = FakeClient(NS(stop_reason="refusal",
                           content=[tool("t1", "remember", {"fact": "x"})]))
    agent, store = make(client)
    reply = agent.send("something")
    assert "can't help" in reply
    assert store.recall("x") == [] and store.recent() == []


def test_truncated_tool_input_is_not_run():
    client = FakeClient(NS(stop_reason="max_tokens",
                           content=[tool("t1", "create_reminder", {"text": "Email", "in_minutes": 5})]))
    agent, store = make(client)
    agent.send("remind me")
    assert store.pending_reminders() == []


def test_join_meeting_action_reaches_the_ui():
    client = FakeClient(
        NS(stop_reason="tool_use", content=[tool("t1", "join_meeting", {"key": "m1"})]),
        NS(stop_reason="end_turn", content=[text("Opening it now.")]),
    )
    agent, _ = make(client)
    opened = []
    agent.send("join my next meeting", on_action=opened.append)
    assert opened == [MEETING.url]
