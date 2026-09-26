"""Launcher kept at the old path so the systemd service and run.sh keep working.
The app itself lives in the deskbuddy package (`python -m deskbuddy` works too)."""

from deskbuddy.app import main

if __name__ == "__main__":
    main()
