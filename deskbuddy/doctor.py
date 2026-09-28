"""`buddy doctor`: is everything Buddy needs on this computer? And `buddy install-hooks`.

Run it after ./setup.sh on a new machine. Each check prints ✓ or ✗ with the fix; missing
REQUIRED things make it exit 1, missing optional ones only turn features off.
Secrets (API key, calendar link) are only checked for presence, never printed."""

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

from .config import ROOT
from .services.speech import DEFAULT_VOICE, TELUGU_VOICE, VOICES

HOME = Path.home()
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
API_KEY = CONFIG / "desktop-buddy" / "api_key"
SETTINGS = CONFIG / "DesktopBuddy" / "DesktopBuddy.conf"
CLAUDE_SETTINGS = HOME / ".claude" / "settings.json"
HOOK_EVENTS = ("UserPromptSubmit", "Stop", "Notification")

# the Ubuntu/Debian packages setup.sh installs; name -> (package, what breaks without it)
COMMANDS = {
    "pw-record": ("pipewire-bin", "voice and meeting recording"),
    "pw-dump": ("pipewire-bin", "finding the call's headset"),
    "pw-metadata": ("pipewire-bin", "following the call's headset"),
    "pw-play": ("pipewire-bin", "spoken replies"),
    "gst-launch-1.0": ("gstreamer1.0-tools", "screen recording"),
    "ffmpeg": ("ffmpeg", "saving meeting recordings"),
    "ffprobe": ("ffmpeg", "saving meeting recordings"),
    "notify-send": ("libnotify-bin", "desktop notifications"),
    "gtk-launch": ("libgtk-3-bin", "opening apps"),
}
GST_ELEMENTS = {"pipewiresrc": "gstreamer1.0-pipewire", "matroskamux": "gstreamer1.0-plugins-good",
                "openh264enc": "gstreamer1.0-plugins-bad"}
PY_MODULES = ["PyQt6", "anthropic", "numpy", "icalendar", "recurring_ical_events", "faster_whisper",
              "piper", "neonize", "segno", "jeepney", "cv2", "rapidocr_onnxruntime"]


def _hook_command():
    return f"{HOME}/.local/bin/buddy claude-hook"


def _is_buddy_hook(hook):
    return str(hook.get("command", "")).rstrip().endswith("buddy claude-hook")


def install_hooks(path=CLAUDE_SETTINGS, command=None):
    """Point Claude Code's hooks at `buddy claude-hook`, keeping every other hook.
    Safe to run again: old Buddy entries (e.g. from another home folder) are replaced."""
    command = command or _hook_command()
    path = Path(path)
    settings = json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}
    hooks = settings.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        groups = []
        for group in hooks.get(event, []):
            kept = [h for h in group.get("hooks", []) if not _is_buddy_hook(h)]
            if kept:
                groups.append({**group, "hooks": kept})
        groups.append({"hooks": [{"type": "command", "command": command, "timeout": 5}]})
        hooks[event] = groups
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_suffix(".json.bak").write_text(path.read_text())
    path.write_text(json.dumps(settings, indent=2) + "\n")
    return path


def hooks_installed(path=CLAUDE_SETTINGS):
    try:
        hooks = json.loads(Path(path).read_text()).get("hooks", {})
    except (OSError, ValueError):
        return False
    return all(any(_is_buddy_hook(h) and Path(h["command"].split()[0]).exists()
                   for g in hooks.get(e, []) for h in g.get("hooks", [])) for e in HOOK_EVENTS)


def _setting(key):
    """A value from Buddy's settings file, read as text (no Qt needed)."""
    try:
        for line in SETTINGS.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def checks():
    """(required, ok, name, fix) for everything Buddy uses."""
    out = []
    missing = [m for m in PY_MODULES if importlib.util.find_spec(m) is None]
    out.append((True, not missing, "Python packages", f"missing {', '.join(missing)}: run ./setup.sh"))
    for cmd, (pkg, use) in COMMANDS.items():
        out.append((cmd in ("pw-record", "notify-send"), bool(shutil.which(cmd)), f"{cmd} ({use})",
                    f"sudo apt install {pkg}"))
    if shutil.which("gst-inspect-1.0"):
        for element, pkg in GST_ELEMENTS.items():
            ok = subprocess.run(["gst-inspect-1.0", element], capture_output=True).returncode == 0
            out.append((False, ok, f"GStreamer {element} (screen recording)", f"sudo apt install {pkg}"))
    for voice in (DEFAULT_VOICE, TELUGU_VOICE):
        out.append((False, (VOICES / f"{voice}.onnx").exists(), f"voice {voice}", "run ./setup.sh (downloads it)"))
    claude_cli = shutil.which("claude") or next(
        (str(p) for p in (HOME / ".local/bin/claude", HOME / ".claude/local/claude") if p.exists()), None)
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY")) or (API_KEY.exists() and API_KEY.stat().st_size > 0)
    out.append((True, has_key or bool(claude_cli), "Claude access (API key or Claude Code)",
                f"save your key in {API_KEY} (chmod 600), or install Claude Code"))
    out.append((False, has_key, "API key (the Ask Buddy assistant)", f"save your key in {API_KEY} (chmod 600)"))
    out.append((False, bool(claude_cli), "Claude Code (coding by voice, hooks)",
                "curl -fsSL https://claude.ai/install.sh | bash"))
    out.append((False, bool(shutil.which("code")), "VS Code (coding by voice)", "install VS Code"))
    out.append((False, bool(_setting("calendar_url")), "Outlook calendar connected",
                "right-click Buddy → Connect Outlook calendar…"))
    buddy = HOME / ".local/bin/buddy"
    out.append((False, buddy.resolve() == (ROOT / "bin" / "buddy").resolve(), "`buddy` command",
                "run ./setup.sh"))
    out.append((False, hooks_installed(), "Claude Code hooks", "buddy install-hooks"))
    enabled = bool(shutil.which("systemctl")) and subprocess.run(
        ["systemctl", "--user", "is-enabled", "desktop-buddy"], capture_output=True, text=True,
    ).stdout.strip() == "enabled"
    out.append((False, enabled, "starts at login", "./setup.sh"))
    return out


def run(print_=print):
    results = checks()
    for required, ok, name, fix in results:
        mark = "✓" if ok else ("✗" if required else "–")
        print_(f" {mark} {name}" + ("" if ok else f"\n      fix: {fix}"))
    bad = [name for required, ok, name, _ in results if required and not ok]
    print_("\nAll required pieces are here." if not bad else f"\nMissing required: {', '.join(bad)}")
    return 1 if bad else 0
