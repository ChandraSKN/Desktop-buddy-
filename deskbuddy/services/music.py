"""Read local MPRIS playback metadata; no audio capture or web requests."""
import json
from urllib.parse import urlparse

from jeepney import DBusAddress, new_method_call
from jeepney.io.blocking import open_dbus_connection

# Chrome/Chromium never publish the page URL over MPRIS, so for a browser that hides it
# any playing media counts (YouTube can't be told apart from other sites there).
BROWSERS = ("chromium", "chrome", "brave", "vivaldi", "opera", "edge", "firefox", "librewolf")


def youtube_playing(properties, player=""):
    def unwrap(value):
        return value[1] if isinstance(value, tuple) and len(value) == 2 else value
    status = unwrap(properties.get("PlaybackStatus"))
    metadata = unwrap(properties.get("Metadata", {}))
    if status != "Playing" or not isinstance(metadata, dict):
        return False
    url = unwrap(metadata.get("xesam:url", ""))
    if not url:
        name = player.removeprefix("org.mpris.MediaPlayer2.").lower()
        return any(browser in name for browser in BROWSERS)
    host = (urlparse(url).hostname or "").lower()
    return host in ("youtube.com", "youtu.be") or host.endswith(".youtube.com")


def playing():
    with open_dbus_connection(bus="SESSION") as bus:
        address = DBusAddress("/org/freedesktop/DBus", "org.freedesktop.DBus", "org.freedesktop.DBus")
        names = bus.send_and_get_reply(new_method_call(address, "ListNames"), timeout=0.5).body[0]
        for name in names:
            if not name.startswith("org.mpris.MediaPlayer2."):
                continue
            try:
                address = DBusAddress("/org/mpris/MediaPlayer2", name, "org.freedesktop.DBus.Properties")
                reply = bus.send_and_get_reply(new_method_call(
                    address, "GetAll", "s", ("org.mpris.MediaPlayer2.Player",)), timeout=0.5)
                if reply.body and isinstance(reply.body[0], dict) and youtube_playing(reply.body[0], name):
                    return True
            except Exception:
                continue  # browser closed or no longer exports media controls
    return False


if __name__ == "__main__":
    try:
        active = playing()
    except Exception:
        active = False
    print(json.dumps({"playing": active}))
