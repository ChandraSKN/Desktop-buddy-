"""Opening things for you: installed applications, folders and web pages.

Only what a person could open themselves from the app menu or file manager: apps come
from their .desktop menu entries (never an arbitrary command), folders must exist under
your home directory, and web addresses must be http(s).

Buddy runs as a systemd service, so anything he started directly would belong to his
service and be killed whenever he restarts. Each launch therefore runs as its own
transient unit (`systemd-run --user`), exactly as if you'd opened it from the dock."""

import configparser
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

HOME = Path.home()
DATA_DIRS = [HOME / ".local/share", *(Path(p) for p in os.environ.get(
    "XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if p),
    Path("/usr/share"), Path("/var/lib/snapd/desktop"), Path("/var/lib/flatpak/exports/share"),
    HOME / ".local/share/flatpak/exports/share"]

# what people call things → words that appear in the app's name/id
ALIASES = {
    "chrome": "google chrome", "google": "google chrome", "vscode": "visual studio code",
    "vs code": "visual studio code", "code": "visual studio code", "files": "nautilus",
    "file manager": "nautilus", "explorer": "nautilus", "file explorer": "nautilus",
    "terminal": "terminal", "command line": "terminal", "console": "terminal",
    "settings": "settings", "control panel": "settings", "calculator": "calculator",
    "notepad": "text editor", "editor": "text editor", "browser": "web browser",
    "internet": "web browser", "mail": "email", "music": "music player",
}
FOLDERS = {"home": "", "downloads": "DOWNLOAD", "download": "DOWNLOAD", "documents": "DOCUMENTS",
           "document": "DOCUMENTS", "desktop": "DESKTOP", "pictures": "PICTURES", "photos": "PICTURES",
           "music": "MUSIC", "videos": "VIDEOS", "movies": "VIDEOS"}
_FILLER = re.compile(r"\b(the|my|app|application|program|folder|directory|please|for me|up)\b")


@dataclass
class App:
    id: str                    # desktop file id, e.g. "org.gnome.Calculator"
    name: str
    generic: str = ""
    keywords: list = field(default_factory=list)
    path: str = ""


def find_apps(dirs=None):
    """Installed applications that show in the menu, first definition of each id wins."""
    apps = {}
    for base in dirs or DATA_DIRS:
        folder = Path(base) / "applications"
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.desktop")):
            app_id = path.stem
            if app_id in apps:
                continue
            entry = _read_entry(path)
            if not entry or entry.get("type", "Application") != "Application":
                continue
            if entry.get("nodisplay", "").lower() == "true" or entry.get("hidden", "").lower() == "true":
                continue
            if not entry.get("name") or not entry.get("exec"):
                continue
            apps[app_id] = App(app_id, entry["name"], entry.get("genericname", ""),
                               [k for k in entry.get("keywords", "").split(";") if k], str(path))
    return list(apps.values())


def _read_entry(path):
    parser = configparser.RawConfigParser(strict=False, interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
        return dict(parser["Desktop Entry"]) if parser.has_section("Desktop Entry") else None
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None


def clean(query):
    q = _FILLER.sub(" ", query.lower())
    q = re.sub(r"[^\w\s.-]", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def score(query, app):
    """0..1: how well query names app."""
    q = ALIASES.get(query, query)
    name, ident = app.name.lower(), app.id.lower().replace("-", " ").replace(".", " ")
    generic = app.generic.lower()
    if q == name or q == ident.split()[-1]:
        return 1.0
    words = q.split()
    best = 0.0
    if name.startswith(q) or all(w in name for w in words):
        best = 0.92
    elif all(w in ident for w in words):
        best = 0.85
    elif q == generic or (generic and all(w in generic for w in words)):
        best = 0.8
    elif any(q == k.lower() for k in app.keywords):
        best = 0.75
    fuzzy = max(SequenceMatcher(None, q, name).ratio(), SequenceMatcher(None, q, ident.split()[-1]).ratio())
    return max(best, fuzzy * 0.9)


def match_app(query, apps, threshold=0.75):
    """(app, score) for the best match, or (None, best score) if nothing is good enough."""
    q = clean(query)
    if not q:
        return None, 0.0
    ranked = sorted(((score(q, a), a) for a in apps), key=lambda t: -t[0])
    if not ranked:
        return None, 0.0
    top, app = ranked[0]
    return (app, top) if top >= threshold else (None, top)


def folder_path(query):
    """An existing folder under your home directory, from a name ("downloads") or a path."""
    q = clean(query)
    if q in FOLDERS:
        key = FOLDERS[q]
        if not key:
            return HOME
        try:
            out = subprocess.run(["xdg-user-dir", key], capture_output=True, text=True, timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        path = Path(out) if out else HOME / q.capitalize()
    else:
        raw = query.strip().strip("'\"")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = HOME / raw
    try:
        path = path.resolve()
    except OSError:
        return None
    if path.is_dir() and (path == HOME or HOME in path.parents):
        return path
    return None


def web_url(text):
    """https URL for "youtube.com", "https://…", else None."""
    t = text.strip().strip("'\"")
    if not re.match(r"^https?://", t, re.I):
        if not re.match(r"^[\w-]+(\.[\w-]+)+(/\S*)?$", t):
            return None
        t = "https://" + t
    parsed = urlparse(t)
    return t if parsed.scheme in ("http", "https") and parsed.netloc else None


def _spawn(argv, label):
    """Run argv in its own systemd scope, outside Buddy's service. A scope (not a transient
    service) because gtk-launch/gio exit as soon as the app has started: a service would
    count as finished then and take the app down with it; a scope lives as long as the app."""
    unit = f"buddy-open-{re.sub(r'[^A-Za-z0-9]', '-', label)[:40]}-{int(time.time() * 1000)}"
    try:
        # no captured output: the app inherits these pipes, and we'd wait until it quits
        subprocess.run(["systemd-run", "--user", "--scope", "--collect", "--quiet", f"--unit={unit}", *argv],
                       check=True, timeout=10, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        return True
    except (OSError, subprocess.SubprocessError):
        try:                                  # no systemd user session: plain detached start
            subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return True
        except OSError:
            return False


class Launcher:
    """The app list is read once and refreshed now and then (new installs)."""

    REFRESH = 300

    def __init__(self, dirs=None, spawn=_spawn):
        self.dirs, self.spawn = dirs, spawn           # tests pass a fake spawn
        self._apps, self._read_at = [], 0.0

    @property
    def apps(self):
        if time.monotonic() - self._read_at > self.REFRESH or not self._apps:
            self._apps, self._read_at = find_apps(self.dirs), time.monotonic()
        return self._apps

    def launch_app(self, app):
        return self.spawn(["gtk-launch", app.id], app.id)

    def open_folder(self, path):
        return self.spawn(["gio", "open", str(path)], Path(path).name or "home")

    def open_url(self, url):
        return self.spawn(["gio", "open", url], urlparse(url).netloc)

    def open(self, what):
        """Open an app, folder or site named by what. Returns (ok, message), or
        (False, None) if nothing matched."""
        folder = folder_path(what) if clean(what) in FOLDERS or "/" in what else None
        if folder:
            return self.open_folder(folder), f"Opening {folder.name or 'your home folder'}."
        app, _ = match_app(what, self.apps)
        if app:
            return self.launch_app(app), f"Opening {app.name}."
        url = web_url(what)
        if url:
            return self.open_url(url), f"Opening {urlparse(url).netloc}."
        return False, None


_OPEN = re.compile(r"^\s*(?:(?:hey|ok|okay)\s+buddy[,.!\s]*)?(?:please\s+|can you\s+|could you\s+)?"
                   r"(open|launch|start|run)\s+(.+?)\s*(?:for me|please)?[.!?]*\s*$", re.I)


def open_request(text):
    """"open firefox" → "firefox"; anything else → None."""
    m = _OPEN.match(text)
    return m.group(2) if m else None
