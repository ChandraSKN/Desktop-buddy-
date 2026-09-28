import json

from deskbuddy import doctor
from deskbuddy.cli import main


def test_install_hooks_keeps_other_hooks_and_replaces_old_buddy_paths(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"model": "x", "hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": "node tracker.cjs"}]},
                 {"hooks": [{"type": "command", "command": "/home/olduser/.local/bin/buddy claude-hook"}]}],
        "SessionEnd": [{"hooks": [{"type": "command", "command": "node tracker.cjs"}]}]}}))
    doctor.install_hooks(path, command="/home/me/.local/bin/buddy claude-hook")
    doctor.install_hooks(path, command="/home/me/.local/bin/buddy claude-hook")     # twice: no duplicates
    s = json.loads(path.read_text())
    commands = {e: [h["command"] for g in groups for h in g["hooks"]] for e, groups in s["hooks"].items()}
    assert s["model"] == "x"
    assert commands["Stop"] == ["node tracker.cjs", "/home/me/.local/bin/buddy claude-hook"]
    assert commands["SessionEnd"] == ["node tracker.cjs"]
    for event in ("UserPromptSubmit", "Notification"):
        assert commands[event] == ["/home/me/.local/bin/buddy claude-hook"]
    assert (tmp_path / "settings.json.bak").exists()


def test_install_hooks_creates_settings(tmp_path):
    path = tmp_path / ".claude" / "settings.json"
    doctor.install_hooks(path, command=f"{tmp_path}/buddy claude-hook")
    assert not doctor.hooks_installed(path)          # the command it points at doesn't exist yet
    (tmp_path / "buddy").write_text("")
    assert doctor.hooks_installed(path)


def test_hooks_installed_with_no_or_broken_settings(tmp_path):
    assert not doctor.hooks_installed(tmp_path / "missing.json")
    (tmp_path / "bad.json").write_text("{not json")
    assert not doctor.hooks_installed(tmp_path / "bad.json")


def test_doctor_exit_status_follows_required_checks(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "checks", lambda: [(True, True, "a", ""), (False, False, "b", "do b")])
    assert main(["doctor"]) == 0
    assert "fix: do b" in capsys.readouterr().out
    monkeypatch.setattr(doctor, "checks", lambda: [(True, False, "a", "do a")])
    assert main(["doctor"]) == 1


def test_real_checks_run_and_never_print_secrets(monkeypatch, tmp_path):
    settings = tmp_path / "DesktopBuddy.conf"
    settings.write_text("[General]\ncalendar_url=https://outlook.example/secret-token\n")
    monkeypatch.setattr(doctor, "SETTINGS", settings)
    lines = []
    doctor.run(lines.append)
    text = "\n".join(lines)
    assert "Outlook calendar connected" in text and "secret-token" not in text
