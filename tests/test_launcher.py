
import pytest

from deskbuddy.services import launcher as L
from deskbuddy.services.agent_tools import ToolBox
from deskbuddy.services.memory import MemoryStore

ENTRIES = {
    "firefox": "Name=Firefox\nGenericName=Web Browser\nExec=firefox %u\nKeywords=internet;web;",
    "org.gnome.Calculator": "Name=Calculator\nExec=gnome-calculator\nKeywords=math;sum;",
    "code": "Name=Visual Studio Code\nGenericName=Text Editor\nExec=code %F",
    "org.gnome.Nautilus": "Name=Files\nExec=nautilus --new-window %U",
    "hidden-helper": "Name=Helper\nExec=helper\nNoDisplay=true",
    "a-link": "Name=Website\nType=Link\nURL=https://x",
    "broken": "Name=Broken",                                        # no Exec
}


@pytest.fixture
def apps_dir(tmp_path):
    folder = tmp_path / "applications"
    folder.mkdir()
    for app_id, body in ENTRIES.items():
        (folder / f"{app_id}.desktop").write_text("[Desktop Entry]\nType=Application\n" + body
                                                  if "Type=" not in body else "[Desktop Entry]\n" + body)
    return tmp_path


@pytest.fixture
def launcher(apps_dir):
    spawned = []
    fake = L.Launcher(dirs=[apps_dir], spawn=lambda argv, label: spawned.append(argv) or True)
    fake.spawned = spawned
    return fake


def test_only_visible_launchable_apps_are_listed(apps_dir):
    names = sorted(a.name for a in L.find_apps([apps_dir]))
    assert names == ["Calculator", "Files", "Firefox", "Visual Studio Code"]


@pytest.mark.parametrize("asked, name", [
    ("firefox", "Firefox"), ("the calculator app", "Calculator"), ("calcuator", "Calculator"),
    ("vs code", "Visual Studio Code"), ("file manager", "Files"), ("web browser", "Firefox"),
])
def test_matching_what_people_say(apps_dir, asked, name):
    app, _ = L.match_app(asked, L.find_apps([apps_dir]))
    assert app is not None and app.name == name


def test_apps_that_are_not_installed_do_not_match(apps_dir):
    assert L.match_app("photoshop", L.find_apps([apps_dir]))[0] is None
    assert L.match_app("", L.find_apps([apps_dir]))[0] is None


@pytest.mark.parametrize("said, what", [
    ("open firefox", "firefox"), ("Hey Buddy, open the calculator please.", "the calculator"),
    ("can you launch VS Code?", "VS Code"), ("Start Files", "Files"),
])
def test_open_requests(said, what):
    assert L.open_request(said) == what


@pytest.mark.parametrize("said", ["what's open today", "is the shop open", "opening hours", ""])
def test_not_open_requests(said):
    assert L.open_request(said) is None


def test_folders_must_be_inside_home(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "HOME", tmp_path)
    (tmp_path / "Projects").mkdir()
    (tmp_path / "escape").symlink_to("/etc")
    assert L.folder_path("Projects") == tmp_path / "Projects"
    assert L.folder_path("home") == tmp_path
    for bad in ("/etc", "../../etc", "escape", "Nope"):
        assert L.folder_path(bad) is None, bad


def test_web_addresses():
    assert L.web_url("youtube.com") == "https://youtube.com"
    assert L.web_url("https://mail.google.com/mail") == "https://mail.google.com/mail"
    for bad in ("youtube", "file:///etc/passwd", "javascript:alert(1)", "ftp://x.com"):
        assert L.web_url(bad) is None, bad


def test_open_routes_to_app_folder_or_site(launcher, monkeypatch, tmp_path):
    assert launcher.open("firefox") == (True, "Opening Firefox.")
    assert launcher.spawned[-1] == ["gtk-launch", "firefox"]
    monkeypatch.setattr(L, "folder_path", lambda q: tmp_path)
    assert launcher.open("downloads")[0] and launcher.spawned[-1] == ["gio", "open", str(tmp_path)]
    assert launcher.open("github.com") == (True, "Opening github.com.")
    assert launcher.open("photoshop") == (False, None)                 # left for Claude


def test_assistant_tools(launcher):
    box = ToolBox(MemoryStore(":memory:"), lambda: [], launcher=launcher)
    assert "Calculator" in box.run("list_apps", {}).content
    assert not box.run("open_app", {"name": "calculator"}).is_error
    assert launcher.spawned[-1] == ["gtk-launch", "org.gnome.Calculator"]
    missing = box.run("open_app", {"name": "photoshop"})
    assert missing.is_error and "list_apps" in missing.content
    assert box.run("open_folder", {"folder": "/etc"}).is_error
    assert box.run("open_website", {"url": "file:///etc/passwd"}).is_error
    assert not box.run("open_website", {"url": "https://youtube.com"}).is_error
    no_launcher = ToolBox(MemoryStore(":memory:"), lambda: [])
    assert no_launcher.run("open_app", {"name": "x"}).is_error


def test_real_launch_runs_outside_buddys_service(monkeypatch):
    calls = []
    monkeypatch.setattr(L.subprocess, "run", lambda argv, **kw: calls.append(argv))
    assert L._spawn(["gtk-launch", "firefox"], "firefox")
    assert calls[0][:5] == ["systemd-run", "--user", "--scope", "--collect", "--quiet"]
    assert calls[0][-2:] == ["gtk-launch", "firefox"]


# ---- in the window
@pytest.fixture
def buddy(qtbot, monkeypatch, launcher):
    from deskbuddy.ui.buddy_window import Buddy
    monkeypatch.setattr(Buddy, "sync_calendar", lambda self: None)
    b = Buddy()
    qtbot.addWidget(b)
    b.calendar_timer.stop()
    b.reminder_timer.stop()
    b.launcher = launcher
    return b


def test_typed_open_is_done_without_claude(buddy, launcher):
    buddy.chat.make_agent = lambda: pytest.fail("no Claude call for a plain 'open'")
    assert buddy.chat.ask("open calculator")
    assert launcher.spawned[-1] == ["gtk-launch", "org.gnome.Calculator"]
    assert "Opening Calculator." in buddy.chat.log.toPlainText()


def test_spoken_open_answers_out_loud(buddy, launcher, monkeypatch):
    said = []
    monkeypatch.setattr(buddy, "voice_reply", said.append)
    buddy.on_heard("Open Firefox.")
    assert said == ["Opening Firefox."] and launcher.spawned[-1] == ["gtk-launch", "firefox"]


def test_unknown_app_goes_to_the_assistant(buddy, monkeypatch):
    from deskbuddy.services import agent as agent_mod
    asked = []

    class FakeAgent:
        def send(self, text, on_text, on_action, spoken=False):
            asked.append(text)
            return "Photoshop isn't installed. Want the website instead?"

    monkeypatch.setattr(agent_mod, "available", lambda: True)
    buddy.chat.make_agent = FakeAgent
    assert buddy.chat.ask("open photoshop")
    buddy.chat.worker.wait(3000)
    assert asked == ["open photoshop"]


def test_buddy_open_command_over_the_socket(buddy, launcher, qtbot):
    import threading

    from deskbuddy import ipc
    result = {}
    t = threading.Thread(target=lambda: result.setdefault(
        "r", ipc.send({"cmd": "open", "what": "files"}, timeout=3, want_reply=True)))
    t.start()
    qtbot.waitUntil(lambda: "r" in result, timeout=3000)
    t.join()
    assert result["r"] == {"ok": True, "message": "Opening Files."}
    assert launcher.spawned[-1] == ["gtk-launch", "org.gnome.Nautilus"]
