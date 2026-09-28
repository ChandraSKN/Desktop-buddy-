"""The `buddy` command: talk to the running Desktop Buddy from a terminal or a script.

    buddy say "Build finished"          show it in his bubble (and wave)
    buddy say --speak "Tests passed"    …and say it out loud
    buddy open firefox                  open an app, folder ("downloads") or site
    buddy show film                     play an animation for a while (film, write)
    buddy status                        what he's doing
    buddy claude-hook                   used by Claude Code hooks (reads JSON on stdin)

Exit status 3 means Buddy isn't running."""

import argparse
import json
import sys

from . import ipc
from .services.claude_hooks import run_hook

NOT_RUNNING = 3


def main(argv=None):
    parser = argparse.ArgumentParser(prog="buddy", description="Talk to Desktop Buddy.")
    sub = parser.add_subparsers(dest="command", required=True)
    say = sub.add_parser("say", help="show a message (and optionally speak it)")
    say.add_argument("text", nargs="+")
    say.add_argument("--speak", action="store_true", help="say it out loud too")
    op = sub.add_parser("open", help="open an installed app, a folder or a website")
    op.add_argument("what", nargs="+")
    show = sub.add_parser("show", help="play an animation: film (recording) or write (minutes)")
    show.add_argument("activity", choices=["film", "write"])
    show.add_argument("--seconds", type=float, default=8)
    sub.add_parser("status", help="print what Buddy is doing")
    sub.add_parser("claude-hook", help="for Claude Code hooks: reads the event JSON on stdin")
    args = parser.parse_args(argv)

    if args.command == "claude-hook":
        run_hook(sys.stdin.read(), ipc.send)
        return 0                                   # never fail a Claude Code hook
    if args.command == "say":
        ok = ipc.send({"cmd": "say", "text": " ".join(args.text), "speak": args.speak})
    elif args.command == "show":
        ok = ipc.send({"cmd": "show", "activity": args.activity, "seconds": args.seconds})
    elif args.command == "open":
        ok = ipc.send({"cmd": "open", "what": " ".join(args.what)}, timeout=15, want_reply=True)
        if ok:
            print(ok.get("message") or "I couldn't find anything by that name.")
            if not ok.get("ok"):
                return 1
    else:
        ok = ipc.send({"cmd": "status"}, want_reply=True)
        if ok:
            print(json.dumps(ok, indent=1))
    if not ok:
        print("Desktop Buddy isn't running.", file=sys.stderr)
        return NOT_RUNNING
    return 0


if __name__ == "__main__":
    sys.exit(main())
