"""Recording follows the devices the call app really uses (e.g. AirPods), not the default."""

import os
import wave

import numpy as np

from deskbuddy.services import recorder as rec


def node(id, media_class, name="", pid=None, app=None, serial=None, **props):
    p = {"media.class": media_class, "node.description": name, "object.serial": serial or 1000 + id}
    if pid is not None:
        p["application.process.id"] = pid
    if app:
        p["application.name"] = app
    p.update(props)
    return {"id": id, "type": "PipeWire:Interface:Node", "info": {"props": p}}


def link(out, into):
    return {"type": "PipeWire:Interface:Link", "info": {"output-node-id": out, "input-node-id": into}}


class Proc:
    def __init__(self, pid):
        self.pid = pid


def graph(our_mic_from=1, our_others_from=2):
    """Laptop mic 1 + speakers 2 are the default; the call (Chrome) uses AirPods 3 + 4.
    Our recorder streams are 20 (mic) and 21 (others)."""
    return [
        node(1, "Audio/Source", "Built-in Audio"), node(2, "Audio/Sink", "Built-in Audio"),
        node(3, "Audio/Source", "AirPods Pro"), node(4, "Audio/Sink", "AirPods Pro"),
        node(10, "Stream/Input/Audio", pid=900, app="Google Chrome"),
        node(11, "Stream/Output/Audio", pid=901, app="Google Chrome"),
        node(20, "Stream/Input/Audio", pid=500), node(21, "Stream/Input/Audio", pid=501,
                                                      **{"stream.capture.sink": "true"}),
        link(3, 10), link(11, 4), link(our_mic_from, 20), link(our_others_from, 21),
    ]


def ours(monkeypatch, pids=(500, 501)):
    monkeypatch.setattr(rec, "_started_by_us", lambda pid: pid in pids)


def test_call_devices_are_the_ones_the_call_app_is_linked_to(monkeypatch):
    ours(monkeypatch)
    found = rec.call_devices(graph())
    assert rec.device_name(found["mic.wav"]) == "AirPods Pro" and found["mic.wav"]["id"] == 3
    assert found["others.wav"]["id"] == 4
    assert rec.other_apps_using_mic(graph()) == ["Google Chrome"]


def test_no_call_no_devices(monkeypatch):
    ours(monkeypatch)
    assert rec.call_devices([o for o in graph() if o.get("id") not in (10, 11)]) == {}


def test_follow_call_moves_our_tracks_to_the_headset(monkeypatch, tmp_path):
    ours(monkeypatch)
    runs = []
    monkeypatch.setattr(rec.subprocess, "run", lambda cmd, **kw: runs.append(cmd))
    r = rec.Recorder(tmp_path)
    r.folder = tmp_path
    (tmp_path / "meta.json").write_text("{}")
    r.procs = [Proc(500), Proc(501)]
    moved = r.follow_call(graph())
    assert moved == [("mic.wav", "AirPods Pro"), ("others.wav", "AirPods Pro")]
    assert ["pw-metadata", "20", "target.object", "1003"] in runs
    assert ["pw-metadata", "21", "target.object", "1004"] in runs
    assert '"AirPods Pro"' in (tmp_path / "meta.json").read_text()
    runs.clear()
    assert r.follow_call(graph(our_mic_from=3, our_others_from=4)) == [] and runs == []


def test_loudness_counts_seconds_with_sound(tmp_path):
    rate = 16000
    audio = np.zeros(rate * 4, dtype=np.int16)
    audio[rate: rate * 2] = (np.sin(np.arange(rate) / 5) * 8000).astype(np.int16)
    path = tmp_path / "t.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
        w.writeframes(audio.tobytes())
    assert rec.loudness(path) == (4.0, 1.0)
    assert rec.loudness(tmp_path / "missing.wav") == (0.0, 0.0)
    assert os.path.exists(path)
