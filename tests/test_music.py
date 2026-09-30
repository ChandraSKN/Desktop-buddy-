import pytest

from deskbuddy.services.music import youtube_playing


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=abc", "https://music.youtube.com/watch?v=x",
                                "https://youtu.be/abc"])
def test_recognizes_playing_youtube(url):
    assert youtube_playing({"PlaybackStatus": ("s", "Playing"),
                            "Metadata": ("a{sv}", {"xesam:url": ("s", url)})})


@pytest.mark.parametrize("status,url", [("Paused", "https://youtube.com/watch?v=x"),
                                       ("Stopped", "https://youtube.com/watch?v=x"),
                                       ("Playing", "https://youtube.com.example.org/video"),
                                       ("Playing", "https://example.org/youtube.com")])
def test_ignores_paused_and_other_sites(status, url):
    assert not youtube_playing({"PlaybackStatus": ("s", status), "Metadata": ("a{sv}", {"xesam:url": ("s", url)})})


def test_missing_metadata_is_inactive():
    assert not youtube_playing({})


@pytest.mark.parametrize("player", ["org.mpris.MediaPlayer2.chromium.instance8076",
                                    "org.mpris.MediaPlayer2.firefox.instance_1_42"])
def test_browser_without_url_counts_while_playing(player):
    chrome = {"xesam:title": ("s", "Manasa - Video Song"), "xesam:artist": ("as", ["Think Music Telugu"])}
    assert youtube_playing({"PlaybackStatus": ("s", "Playing"), "Metadata": ("a{sv}", chrome)}, player)
    assert not youtube_playing({"PlaybackStatus": ("s", "Paused"), "Metadata": ("a{sv}", chrome)}, player)


def test_non_browser_without_url_is_ignored():
    props = {"PlaybackStatus": ("s", "Playing"), "Metadata": ("a{sv}", {"xesam:title": ("s", "x")})}
    assert not youtube_playing(props, "org.mpris.MediaPlayer2.vlc")
    assert not youtube_playing(props)
