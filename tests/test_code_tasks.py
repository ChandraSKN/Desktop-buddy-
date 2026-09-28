import json
import stat
from pathlib import Path

import pytest

from deskbuddy.services import code_tasks
from deskbuddy.services.agent_tools import ToolBox
from deskbuddy.services.claude_hooks import handle
from deskbuddy.services.code_tasks import (
    CodeTask,
    command,
    find_projects,
    match_project,
    parse_line,
    step,
    summary,
)
from deskbuddy.services.memory import MemoryStore
from deskbuddy.ui import code_task as code_ui
from deskbuddy.ui.buddy_window import Buddy

HOME = Path.home()
PROJECTS = [HOME / "Documents/chandra/desktop-buddy", HOME / "Documents/chandra/founder-os",
            HOME / "Documents/chandra/chandranadendla porfolio", HOME / "video-editor"]


# ---- finding the project

def test_projects_are_folders_with_a_marker_and_their_insides_are_skipped(tmp_path):
    (tmp_path / "work/app/.git").mkdir(parents=True)
    (tmp_path / "work/app/src/inner").mkdir(parents=True)
    (tmp_path / "work/app/src/inner/package.json").write_text("{}")
    (tmp_path / "site").mkdir()
    (tmp_path / "site/index.html").write_text("")
    (tmp_path / "node_modules/lib").mkdir(parents=True)
    (tmp_path / "node_modules/lib/package.json").write_text("{}")
    (tmp_path / "notes").mkdir()
    found = {p.relative_to(tmp_path).as_posix() for p in find_projects([tmp_path])}
    assert found == {"work/app", "site"}


@pytest.mark.parametrize("query, name", [
    ("desktop buddy", "desktop-buddy"), ("the desktop-buddy project", "desktop-buddy"),
    ("Founder OS", "founder-os"), ("portfolio", "chandranadendla porfolio"),
    ("video editor", "video-editor"), ("desktop body", "desktop-buddy"),
])
def test_spoken_names_find_the_project(query, name):
    assert match_project(query, PROJECTS).name == name


def test_unknown_names_and_paths_outside_home_find_nothing():
    assert match_project("rocket launcher", PROJECTS) is None
    assert match_project("/etc", PROJECTS) is None
    assert match_project("~/../..", PROJECTS) is None


# ---- running Claude Code

def test_the_command_runs_headless_with_full_permissions_and_resumes():
    argv = command("/bin/claude", CodeTask(Path("/p"), "add tests", resume="abc"))
    assert argv[:3] == ["/bin/claude", "-p", "add tests"]
    assert "bypassPermissions" in argv and "stream-json" in argv
    assert argv[-2:] == ["--resume", "abc"]
    assert "--resume" not in command("/bin/claude", CodeTask(Path("/p"), "x"))


def test_steps_read_like_a_progress_log():
    assert step("Read", {"file_path": "/a/b/styles.py"}) == "📖 Reading styles.py"
    assert step("Edit", {"file_path": "/a/b/voice.py"}) == "✏️ Editing voice.py"
    assert step("Bash", {"command": "pytest -q", "description": "Run the tests"}) == "▶ Run the tests"
    assert step("TodoWrite", {}) is None


def test_stream_json_lines_become_session_steps_and_result():
    assert parse_line(json.dumps({"type": "system", "subtype": "init", "session_id": "s1"})) == [("session", "s1")]
    line = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Let me look"},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}})
    assert parse_line(line) == [("step", "▶ ls")]
    done = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                       "result": "Added it.", "session_id": "s1"})
    assert parse_line(done) == [("session", "s1"), ("result", True, "Added it.")]
    assert parse_line("not json") == []


def test_summary_is_the_opening_without_code_or_markdown():
    text = "Added **dark mode** to the chat panel. All 221 tests pass.\n\n```diff\n+x\n```\nDetails…"
    assert summary(text) == "Added dark mode to the chat panel. All 221 tests pass."
    long = "This sentence is long enough to be kept whole here. " * 10
    assert summary(long).endswith(".") and len(summary(long)) <= 260


def test_buddys_own_tasks_dont_trigger_the_claude_code_hooks(monkeypatch, tmp_path):
    monkeypatch.setenv("BUDDY_CODE_TASK", "1")
    assert handle({"hook_event_name": "Notification", "message": "hi"}, tmp_path) is None


# ---- the assistant's tools

class FakeRunner:
    busy = False

    def __init__(self):
        self.proposed = []

    def plan(self, folder, task, fresh=False):
        return CodeTask(folder, task, None if fresh else "prev")

    def propose(self, task):
        self.proposed.append(task)

    def snapshot(self):
        return None, []


def toolbox(runner, spawned=None):
    class Launcher:
        def spawn(self, argv, label):
            spawned.append(argv)
            return True
    return ToolBox(MemoryStore(":memory:"), lambda: [], launcher=Launcher(), code=runner,
                   projects=lambda: PROJECTS)


def test_a_code_task_is_only_proposed():
    runner = FakeRunner()
    result = toolbox(runner).run("start_code_task", {"project": "desktop buddy", "task": "add dark mode"})
    assert not result.is_error and "NOT started" in result.content
    assert runner.proposed == [CodeTask(PROJECTS[0], "add dark mode", "prev")]
    toolbox(runner).run("start_code_task", {"project": "founder os", "task": "x", "fresh": True})
    assert runner.proposed[-1].resume is None


def test_no_new_task_while_one_runs_and_unknown_projects_are_errors():
    runner = FakeRunner()
    assert toolbox(runner).run("start_code_task", {"project": "nowhere", "task": "x"}).is_error
    runner.busy = True
    assert toolbox(runner).run("start_code_task", {"project": "founder os", "task": "x"}).is_error
    assert runner.proposed == []


def test_open_in_vscode_opens_the_folder():
    spawned = []
    assert not toolbox(FakeRunner(), spawned).run("open_in_vscode", {"project": "video editor"}).is_error
    assert spawned == [["code", str(HOME / "video-editor")]]


# ---- the card and the real runner, with a stand-in for `claude`

FAKE_CLAUDE = """#!/bin/sh
echo '{"type":"system","subtype":"init","session_id":"sess-1"}'
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"x/app.py"}}]}}'
echo '{"type":"result","subtype":"success","is_error":false,"result":"Fixed the bug. Tests pass.",\
"session_id":"sess-1"}'
"""


@pytest.fixture
def buddy(qtbot, monkeypatch, tmp_path):
    fake = tmp_path / "claude"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(code_ui, "claude_path", lambda: str(fake))
    monkeypatch.setattr(Buddy, "sync_calendar", lambda self: None)
    b = Buddy()
    qtbot.addWidget(b)
    b.show()
    b.calendar_timer.stop()
    b.reminder_timer.stop()
    b.spawned = []
    monkeypatch.setattr(b.launcher, "spawn", lambda argv, label: b.spawned.append(argv) or True)
    monkeypatch.setattr(b.notifier, "notify", lambda *a, **k: None)
    yield b
    b.code.stop()
    b.commands.server.close()          # or later tests find a "running" Buddy on the socket


def test_yes_opens_vscode_runs_claude_code_and_reports(buddy, qtbot, tmp_path):
    task = buddy.code.plan(tmp_path, "fix the bug")
    buddy.on_code_proposed(task)
    assert buddy.code_card.isVisible() and not buddy.code.busy
    assert buddy.chat.ask("yes go ahead")
    assert buddy.spawned == [["code", str(tmp_path)]]
    qtbot.waitUntil(lambda: not buddy.code.busy, timeout=5000)
    assert buddy.code_card.steps == ["✏️ Editing app.py"]
    assert "Fixed the bug. Tests pass." in buddy.bubble.text
    again = buddy.code.plan(tmp_path, "now commit it")
    assert again.resume == "sess-1"                     # a follow-up continues the session


def test_no_cancels_the_task(buddy, tmp_path):
    buddy.on_code_proposed(buddy.code.plan(tmp_path, "delete everything"))
    buddy.on_heard("No.")
    assert not buddy.code_card.isVisible() and buddy.spawned == [] and not buddy.code.busy


def test_the_prompt_mentions_the_confirmation(monkeypatch):
    from deskbuddy.services.agent import SYSTEM
    assert "start_code_task" in SYSTEM and "never claim it's done" in SYSTEM
    assert code_tasks.CONTINUE_WITHIN == 3600
