#!/bin/bash
# Start Desktop Buddy. Uses the background service when it's installed (setup.sh --autostart),
# otherwise starts it detached, so closing the terminal doesn't kill him.
cd "$(dirname "$0")"
if systemctl --user cat desktop-buddy.service >/dev/null 2>&1; then
    systemctl --user restart desktop-buddy.service && echo "Buddy started (background service)."
    exit
fi
pgrep -f "desktop-buddy/.venv/bin/python.*buddy.py" >/dev/null && { echo "Buddy is already running."; exit 0; }
nohup "$PWD/.venv/bin/python" "$PWD/buddy.py" >/dev/null 2>&1 &
