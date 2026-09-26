"""Speech to text for a recording folder, on this machine (faster-whisper, CPU).

Run as a separate, low-priority process so a long meeting doesn't stall Buddy:

    nice -n 15 python -m deskbuddy.services.transcribe <recording folder>

It prints `{"progress": 0.42}` lines and writes transcript.json:
[{"start": 12.3, "end": 15.0, "speaker": "You" | "Others", "text": "..."}, ...].

The multilingual model handles Telugu/Hindi/English code-switching (language is detected
per segment). Without a headset your mic also hears the other people through the speakers,
so a mic segment that repeats an overlapping "Others" segment is dropped as echo.

Telugu: in a mostly-English meeting Whisper writes Telugu speech in English letters
("Ravi export bugs Budawaram lopu fix chesthadu"), which keeps names and Claude reads well.
When it decides a stretch *is* Telugu it either gets stuck repeating syllables in Telugu
script or writes the sounds in another script (Devanagari); such a stretch is re-run as an
English translation (marked "(translated)") instead of being dropped or kept unreadable.

Decoding runs once per segment (temperature 0). Whisper's default retries at higher
temperatures made a quiet, noisy mic track take 6x longer and then threw the result away;
instead, a segment that fails Whisper's own quality limits is dropped here (looks_real)."""

import json
import sys
import wave
from difflib import SequenceMatcher
from pathlib import Path

MODEL = "small"                # multilingual; ~460 MB, downloaded once to ~/.cache/huggingface
THREADS = 4                    # leave the rest of the CPU for the user
SPEAKERS = {"mic.wav": "You", "others.wav": "Others"}


def duration(path):
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
    except (OSError, EOFError, wave.Error):
        return 0.0


def mostly_latin(text):
    letters = [c for c in text if c.isalpha()]
    return not letters or sum(c.isascii() for c in letters) / len(letters) >= 0.6


def is_loop(seg):
    """Stuck repeating (not silence): worth retrying as a translation."""
    return seg.compression_ratio > 2.4 and seg.no_speech_prob <= 0.6


def looks_real(seg):
    """Whisper's own quality limits: repetitive (hallucinated) text, likely silence, or
    very low confidence."""
    return (seg.compression_ratio <= 2.4 and seg.no_speech_prob <= 0.6
            and seg.avg_logprob >= -1.0 and seg.text.strip())


def _overlap(a, b):
    return max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))


def is_echo(mine, theirs):
    """mine (a "You" segment) mostly repeats an overlapping "Others" segment."""
    length = max(0.01, mine["end"] - mine["start"])
    for other in theirs:
        if _overlap(mine, other) / length < 0.4:
            continue
        ratio = SequenceMatcher(None, mine["text"].lower(), other["text"].lower()).ratio()
        if ratio > 0.55:
            return True
    return False


def merge(tracks):
    """tracks: {"You": [segments], "Others": [segments]} -> one timeline without echo."""
    you = [s for s in tracks.get("You", []) if not is_echo(s, tracks.get("Others", []))]
    merged = you + list(tracks.get("Others", []))
    merged.sort(key=lambda s: s["start"])
    return [s for s in merged if s["text"].strip()]


def as_text(segments):
    def stamp(t):
        return f"{int(t // 3600):d}:{int(t // 60 % 60):02d}:{int(t % 60):02d}"
    return "\n".join(f"[{stamp(s['start'])}] {s['speaker']}: {s['text'].strip()}" for s in segments)


def _translate(model, audio, start, end):
    """English translation of audio[start:end] (seconds), or None."""
    clip = audio[int(start * 16000):int(end * 16000)]
    if len(clip) < 8000:
        return None
    segments, _ = model.transcribe(clip, task="translate", condition_on_previous_text=False, temperature=0.0)
    text = " ".join(s.text.strip() for s in segments if looks_real(s))
    return f"(translated) {text}" if text else None


def transcribe_folder(folder, report=lambda p: None):
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio

    folder = Path(folder)
    files = [f for f in SPEAKERS if duration(folder / f) > 1.0]
    total = sum(duration(folder / f) for f in files) or 1.0
    model = WhisperModel(MODEL, device="cpu", compute_type="int8", cpu_threads=THREADS)
    done, tracks = 0.0, {}
    for name in files:
        audio = decode_audio(str(folder / name))
        segments, _ = model.transcribe(audio, vad_filter=True, multilingual=True,
                                       condition_on_previous_text=False, temperature=0.0)
        out = []
        for seg in segments:
            report(min(0.99, (done + seg.end) / total))
            text = seg.text.strip() if looks_real(seg) and mostly_latin(seg.text) else None
            if text is None and (is_loop(seg) or looks_real(seg)):
                text = _translate(model, audio, seg.start, seg.end)
            if text:
                out.append({"start": round(seg.start, 2), "end": round(seg.end, 2),
                            "speaker": SPEAKERS[name], "text": text})
        done += duration(folder / name)
        tracks[SPEAKERS[name]] = out
    result = merge(tracks)
    (folder / "transcript.json").write_text(json.dumps(result, ensure_ascii=False, indent=0))
    return result


if __name__ == "__main__":
    transcribe_folder(sys.argv[1], lambda p: print(json.dumps({"progress": round(p, 3)}), flush=True))
    print(json.dumps({"progress": 1.0}), flush=True)
