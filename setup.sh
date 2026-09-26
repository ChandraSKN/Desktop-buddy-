#!/bin/bash
# One-time setup for Desktop Buddy.
#   ./setup.sh              install everything
#   ./setup.sh --autostart  also start the buddy automatically when you log in
set -e
cd "$(dirname "$0")"
DIR="$PWD"

echo "==> Creating Python virtual environment (.venv)"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
echo "==> Installing dependencies (PyQt6, anthropic, Pillow)"
.venv/bin/pip install -q -r requirements.txt

echo "==> Building sprite.png from assets/Hero_image.png"
.venv/bin/python make_sprite.py

echo "==> Writing launcher entry"
chmod +x run.sh
cat > desktop-buddy.desktop <<DESKTOP
[Desktop Entry]
Type=Application
Name=Desktop Buddy
Comment=Wandering desktop companion that fixes your text
Exec=$DIR/run.sh
Icon=$DIR/sprite.png
X-GNOME-Autostart-enabled=true
DESKTOP
mkdir -p ~/.local/share/applications
cp desktop-buddy.desktop ~/.local/share/applications/

if [ "$1" = "--autostart" ]; then
    # Background service: starts at every login, restarts itself if it ever crashes.
    echo "==> Installing background service (starts at login)"
    sed -e "s#^WorkingDirectory=.*#WorkingDirectory=$DIR#" \
        -e "s#^ExecStart=.*#ExecStart=$DIR/.venv/bin/python $DIR/buddy.py#" \
        desktop-buddy.service > desktop-buddy.service.tmp && mv desktop-buddy.service.tmp desktop-buddy.service
    mkdir -p ~/.config/systemd/user
    cp desktop-buddy.service ~/.config/systemd/user/
    rm -f ~/.config/autostart/desktop-buddy.desktop      # older autostart method
    systemctl --user daemon-reload
    systemctl --user enable desktop-buddy.service
    systemctl --user restart desktop-buddy.service
    echo "==> Autostart enabled (systemctl --user status desktop-buddy)"
fi

if ! command -v claude >/dev/null && [ -z "$ANTHROPIC_API_KEY" ] && [ ! -s ~/.config/desktop-buddy/api_key ]; then
    echo
    echo "NOTE: No Claude access found, so corrections will use the basic LanguageTool checker."
    echo "      Install/log in to Claude Code, or save an API key to ~/.config/desktop-buddy/api_key"
fi

echo
echo "Done! Start the buddy with:  ./run.sh   (or search 'Desktop Buddy' in the app menu)"
