#!/bin/bash
# Set up Desktop Buddy on this computer (Ubuntu/Debian, GNOME). Safe to run again at any
# time: it only adds what's missing and re-points everything at this folder, so it's also
# what you run after moving the folder or copying it to a new machine.
#   ./setup.sh                  everything, and start Buddy at every login
#   ./setup.sh --no-autostart   everything except the login service
#   ./setup.sh --no-apt         don't install system packages (no sudo)
# Your data (memory, settings, API key, minutes) is separate: see scripts/backup.sh.
set -eo pipefail
cd "$(dirname "$0")"
DIR="$PWD"
AUTOSTART=1 APT=1
for arg in "$@"; do
    case "$arg" in
        --no-autostart) AUTOSTART=0 ;;
        --autostart) AUTOSTART=1 ;;                  # the old flag; now the default
        --no-apt) APT=0 ;;
        *) echo "unknown option: $arg"; exit 2 ;;
    esac
done

# pipewire: mic/speaker; gstreamer: screen recording; ffmpeg: saving recordings;
# libnotify/libgtk: notifications and opening apps; libxcb-cursor0: Qt on X11
PACKAGES="python3-venv pipewire-bin wireplumber gstreamer1.0-tools gstreamer1.0-pipewire
          gstreamer1.0-plugins-good gstreamer1.0-plugins-bad ffmpeg libnotify-bin libgtk-3-bin
          libxcb-cursor0 curl"
if [ "$APT" = 1 ] && command -v apt-get >/dev/null; then
    MISSING=$(for p in $PACKAGES; do dpkg -s "$p" >/dev/null 2>&1 || echo "$p"; done | xargs)
    if [ -n "$MISSING" ]; then
        echo "==> Installing system packages: $MISSING"
        sudo apt-get install -y $MISSING
    fi
fi

echo "==> Python environment (.venv)"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -q -r requirements.txt
# rapidocr pulls the GUI OpenCV, whose bundled Qt clashes with PyQt6: keep only the headless one
if .venv/bin/pip show -q opencv-python 2>/dev/null; then
    .venv/bin/pip uninstall -y -q opencv-python
    .venv/bin/pip install -q --force-reinstall --no-deps opencv-python-headless
fi

echo "==> Voices (Piper)"
VOICES="$HOME/.local/share/desktop-buddy/voices"
mkdir -p "$VOICES"
for voice in en/en_US/ryan/medium/en_US-ryan-medium te/te_IN/venkatesh/medium/te_IN-venkatesh-medium; do
    name=$(basename "$voice")
    for ext in onnx onnx.json; do
        [ -s "$VOICES/$name.$ext" ] && continue
        echo "    downloading $name.$ext"
        curl -fsSL -o "$VOICES/$name.$ext.part" \
            "https://huggingface.co/rhasspy/piper-voices/resolve/main/$voice.$ext"
        mv "$VOICES/$name.$ext.part" "$VOICES/$name.$ext"
    done
done

echo "==> Speech recognition models (Whisper tiny.en, base, small; ~700 MB the first time)"
.venv/bin/python -c "
from faster_whisper import download_model
for m in ('tiny.en', 'base', 'small'):
    download_model(m)
"

echo "==> App menu entry and the 'buddy' command"
chmod +x run.sh bin/buddy
mkdir -p ~/.local/share/applications ~/.local/bin
sed -e '/^# Template/d' -e "s#@DIR@#$DIR#g" desktop-buddy.desktop > ~/.local/share/applications/desktop-buddy.desktop
ln -sf "$DIR/bin/buddy" ~/.local/bin/buddy

if [ -x ~/.local/bin/claude ] || command -v claude >/dev/null; then
    echo "==> Claude Code hooks (Buddy tells you when a long Claude task finishes)"
    bin/buddy install-hooks
fi

if [ "$AUTOSTART" = 1 ]; then
    # Background service: starts at every login, restarts itself if it ever crashes.
    echo "==> Background service (starts at login)"
    mkdir -p ~/.config/systemd/user
    sed -e '/^# Template/d' -e "s#@DIR@#$DIR#g" desktop-buddy.service > ~/.config/systemd/user/desktop-buddy.service
    rm -f ~/.config/autostart/desktop-buddy.desktop      # older autostart method
    systemctl --user daemon-reload
    systemctl --user enable desktop-buddy.service
    systemctl --user restart desktop-buddy.service
fi

echo
echo "==> Checking everything"
bin/buddy doctor || true
echo
if [ "$AUTOSTART" = 1 ]; then
    echo "Done! Buddy is running and will start at every login."
else
    echo "Done! Start the buddy with:  ./run.sh   (or search 'Desktop Buddy' in the app menu)"
fi
