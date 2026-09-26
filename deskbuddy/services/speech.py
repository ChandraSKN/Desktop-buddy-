"""Buddy's voice: text to speech on this computer (Piper), plus mouth shapes for lip-sync.

Piper renders a sentence in a fraction of a second on the CPU. Its voices don't report
phoneme timings, so the mouth follows loudness instead: the audio is cut into 40 ms
frames and each gets one of four openings (closed … wide) from its RMS level, the usual
approach for cartoon characters."""

import re
import wave
from pathlib import Path

import numpy as np

VOICES = Path.home() / ".local" / "share" / "desktop-buddy" / "voices"
DEFAULT_VOICE = "en_US-ryan-medium"
MOUTH_FPS = 25
MOUTH_LEVELS = 4

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def clean_for_speech(text):
    """What a person would say out loud: no markdown, links, emoji or bullet symbols."""
    text = re.sub(r"https?://\S+", "the link", text)
    text = re.sub(r"[*_`#>]+", "", text)
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.M)
    text = _EMOJI.sub("", text)
    text = text.replace("–", " to ").replace("—", ", ")
    return re.sub(r"\s+", " ", text).strip()


def mouth_levels(samples, rate, fps=MOUTH_FPS):
    """Mouth opening (0 … MOUTH_LEVELS-1) for each 1/fps of int16 samples."""
    samples = np.asarray(samples, dtype=np.float32) / 32768.0
    hop = max(1, int(rate / fps))
    frames = len(samples) // hop
    if frames == 0:
        return []
    rms = np.sqrt(np.mean(samples[:frames * hop].reshape(frames, hop) ** 2, axis=1))
    loud = np.percentile(rms, 95) or 1.0
    norm = rms / loud
    levels = np.digitize(norm, [0.12, 0.35, 0.65])          # silence, soft, normal, loud
    # a mouth doesn't snap shut mid-word: close only after two quiet frames
    out, quiet = [], 0
    for level in levels:
        quiet = quiet + 1 if level == 0 else 0
        out.append(int(level) if level or quiet >= 2 or not out else max(0, out[-1] - 1))
    return out


class Voice:
    def __init__(self, name=DEFAULT_VOICE):
        self.name = name
        self._voice = None

    def available(self):
        return (VOICES / f"{self.name}.onnx").exists()

    def synthesize(self, text, path):
        """Write speech for text to a WAV at path. Returns (mouth levels, seconds)."""
        from piper import PiperVoice

        if self._voice is None:
            self._voice = PiperVoice.load(str(VOICES / f"{self.name}.onnx"))
        chunks = list(self._voice.synthesize(text))
        if not chunks:
            return [], 0.0
        rate = chunks[0].sample_rate
        pcm = np.concatenate([c.audio_int16_array for c in chunks])
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        return mouth_levels(pcm, rate), len(pcm) / rate
