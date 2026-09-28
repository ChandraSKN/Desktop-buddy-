"""Coding tasks for Claude Code, given to Buddy by voice or chat.

"In desktop-buddy, add a dark mode and run the tests": the assistant picks the project
folder and writes the task down; the user sees both on a card and says yes (or clicks
Run). Only then does Buddy open the folder in VS Code and run Claude Code on it headless
(`claude -p --output-format stream-json`), with full permissions: it may edit files and
run any command. The card shows each step as it happens and Buddy says the summary.

The confirmation is the safety: a misheard sentence, or a meeting invite that tries to
instruct the assistant, can at most put a card on screen. "Now commit it" within an hour
continues the same Claude Code session (--resume), so it knows what it just did.

Projects are folders under your home directory; nothing here starts a process, so it's
testable without Claude Code (ui/code_task.py runs it)."""

import json
import os
import re
import shutil
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

HOME = Path.home()
CONTINUE_WITHIN = 60 * 60          # seconds: a task this soon after the last one continues it
# what makes a folder a project, and where to look for them (a few levels down)
MARKERS = (".git", "package.json", "pyproject.toml", "requirements.txt", "setup.py", "Cargo.toml",
           "go.mod", "pom.xml", "build.gradle", "CMakeLists.txt", "Makefile", "index.html", "app.py")
SKIP = {"node_modules", "__pycache__", "venv", ".venv", "site-packages", "dist", "build", "out",
        "snap", "Trash", "go", ".cache"}
MAX_DEPTH = 3

VOICE_NOTE = ("This task was given by voice through Desktop Buddy (speech recognition, so "
              "names may be misspelt). The user can't answer questions while you work: make "
              "sensible choices and say what you chose. Begin your final message with one or "
              "two plain sentences that sum up what you did, to be read aloud; details after.")


def claude_path():
    return shutil.which("claude") or next(
        (str(p) for p in (HOME / ".local/bin/claude", HOME / ".claude/local/claude") if p.exists()), None)


def find_projects(roots=None, max_depth=MAX_DEPTH):
    """Project folders under the home directory, most recently changed first."""
    found = []

    def walk(folder, depth):
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        if depth > 0 and any(e.name in MARKERS for e in entries):
            found.append(Path(folder))
            return                          # a project's own sub-folders aren't projects
        if depth >= max_depth:
            return
        for e in entries:
            if (e.is_dir(follow_symlinks=False) and not e.name.startswith(".")
                    and e.name not in SKIP):
                walk(e.path, depth + 1)

    for root in roots or [HOME]:
        walk(root, 0)

    def changed(p):
        try:
            return p.stat().st_mtime
        except OSError:
            return 0

    return sorted(set(found), key=changed, reverse=True)


def _norm(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def match_project(query, projects):
    """The project folder `query` names ("desktop buddy", "the portfolio", a path), or None.
    Paths must exist and stay inside the home directory."""
    raw = query.strip().strip("'\"")
    if "/" in raw or raw.startswith("~"):
        path = Path(raw).expanduser()
        path = (path if path.is_absolute() else HOME / raw)
        try:
            path = path.resolve()
        except OSError:
            return None
        return path if path.is_dir() and HOME in path.parents else None
    q = _norm(re.sub(r"\b(the|my|project|folder|repo|app)\b", " ", raw.lower()))
    if not q:
        return None
    best, best_score = None, 0.0
    for p in projects:
        name = _norm(p.name)
        if q == name or q.replace(" ", "") == name.replace(" ", ""):
            return p
        words = name.split()
        if all(w in name for w in q.split()):
            score = 0.9
        elif all(max((SequenceMatcher(None, w, n).ratio() for n in words), default=0) >= 0.8
                 for w in q.split()):
            score = 0.85                    # "portfolio" for a folder spelt "porfolio"
        else:
            score = 0.0
        score = max(score, SequenceMatcher(None, q.replace(" ", ""), name.replace(" ", "")).ratio())
        if score > best_score:
            best, best_score = p, score
    return best if best_score >= 0.72 else None


@dataclass(frozen=True)
class CodeTask:
    folder: Path
    task: str
    resume: str | None = None       # Claude Code session id to continue


def command(claude, task):
    """argv for running `task` headless with full permissions."""
    argv = [claude, "-p", task.task, "--output-format", "stream-json", "--verbose",
            "--permission-mode", "bypassPermissions", "--append-system-prompt", VOICE_NOTE]
    if task.resume:
        argv += ["--resume", task.resume]
    return argv


def _short(text, n=70):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _file(path):
    return Path(str(path)).name or str(path)


def step(tool, args):
    """One line for the card about a tool call, or None for bookkeeping ones."""
    args = args if isinstance(args, dict) else {}
    if tool in ("Read", "NotebookRead"):
        return f"📖 Reading {_file(args.get('file_path', ''))}"
    if tool in ("Edit", "MultiEdit", "Write", "NotebookEdit"):
        verb = "Writing" if tool == "Write" else "Editing"
        return f"✏️ {verb} {_file(args.get('file_path') or args.get('notebook_path', ''))}"
    if tool == "Bash":
        return f"▶ {_short(args.get('description') or args.get('command', ''))}"
    if tool in ("Grep", "Glob", "LS"):
        return f"🔎 Searching {_short(args.get('pattern') or args.get('path', ''), 40)}"
    if tool in ("WebFetch", "WebSearch"):
        return f"🌐 {_short(args.get('query') or args.get('url', ''), 50)}"
    if tool in ("Task", "Agent"):
        return f"🤖 {_short(args.get('description', 'Helper agent'), 50)}"
    if tool in ("TodoWrite", "ToolSearch", "Skill"):
        return None
    return f"🔧 {tool}"


def parse_line(line):
    """A stream-json line → list of ("session", id) / ("step", text) / ("result", ok, text)."""
    try:
        event = json.loads(line)
    except ValueError:
        return []
    if not isinstance(event, dict):
        return []
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init" and event.get("session_id"):
        return [("session", event["session_id"])]
    if kind == "assistant":
        out = []
        for block in (event.get("message") or {}).get("content") or []:
            if block.get("type") == "tool_use":
                text = step(block.get("name", ""), block.get("input"))
                if text:
                    out.append(("step", text))
        return out
    if kind == "result":
        ok = event.get("subtype") == "success" and not event.get("is_error")
        text = str(event.get("result") or ("Done." if ok else "Claude Code stopped with an error."))
        return [("session", event["session_id"])] * bool(event.get("session_id")) + [("result", ok, text)]
    return []


def summary(result, limit=260):
    """The first sentence or two of Claude Code's final message, for speaking."""
    text = re.sub(r"```.*?```", " ", result, flags=re.S)
    text = re.sub(r"[`*_#>]", "", text)
    first = text.strip().split("\n\n")[0]
    first = " ".join(first.split())
    if len(first) <= limit:
        return first
    cut = first[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 60 else cut.rstrip() + "…"
