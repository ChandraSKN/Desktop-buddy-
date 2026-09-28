"""Who was speaking when, from the screen recording of a Teams call. Separate process:

    nice -n 15 python -m deskbuddy.services.speakers <recording folder>

Teams draws a coloured (blue/purple) border around the tile of whoever is talking, with
their name at the bottom-left of the tile. Once a second we look for a hollow rectangle
in that colour, big enough to be a video tile, and read the name in it (RapidOCR, on
this computer). Writes speakers.json: [{"start": 12.0, "end": 19.0, "name": "Rahul Sharma"}]
in seconds of the screen recording.

Then name_segments() puts those names on the "Others" transcript segments."""

import json
import re
import subprocess
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

SAMPLE_EVERY = 1.0             # seconds between looked-at frames
WIDTH = 1280                   # frames are scaled to this width before looking
REREAD_EVERY = 10              # seconds before re-reading the name of the same tile
# Teams' speaking border in OpenCV HSV (H 0-180): blue to blue-violet, fairly saturated
HUE = (95, 135)
MIN_SAT, MIN_VAL = 90, 110
NOISE = re.compile(r"\((guest|external|unverified|organi[sz]er|presenter)\)", re.I)


def highlight_boxes(frame):
    """Hollow blue rectangles the size of a video tile: [(x, y, w, h)], biggest first."""
    import cv2
    import numpy as np
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([HUE[0], MIN_SAT, MIN_VAL]), np.array([HUE[1], 255, 255]))
    # drop solid blue things (buttons, avatars) so one touching a border can't hide it:
    # a thin border vanishes under this erosion, a solid shape survives and is cut out
    kernel = np.ones((9, 9), np.uint8)
    solid = cv2.dilate(cv2.erode(mask, kernel), kernel, iterations=2)
    mask[solid > 0] = 0
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    fh, fw = mask.shape
    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < fw * 0.08 or h < fh * 0.08 or not 0.5 < w / h < 3.0:
            continue                             # too small or wrong shape for a tile
        box = mask[y:y + h, x:x + w] > 0
        if box.mean() > 0.35:
            continue                             # filled (a button, an avatar), not a border
        band = max(2, min(w, h) // 40)
        edges = [box[:band].any(axis=0).mean(), box[-band:].any(axis=0).mean(),
                 box[:, :band].any(axis=1).mean(), box[:, -band:].any(axis=1).mean()]
        if min(edges) < 0.6:
            continue                             # not a closed frame on all four sides
        boxes.append((x, y, w, h))
    return sorted(boxes, key=lambda b: -b[2] * b[3])


def clean_name(texts):
    """OCR pieces from a tile's name label -> a person's name, or ''."""
    words = []
    for text in texts:
        text = NOISE.sub("", text)
        text = re.sub(r"[^A-Za-z .,'\-()]", " ", text)
        words.extend(w for w in text.replace("(", " ").replace(")", " ").split() if len(w) > 1 or w.isupper())
    name = " ".join(words).strip(" .,-'")
    return name if sum(c.isalpha() for c in name) >= 3 else ""


def read_name(frame, box, ocr):
    x, y, w, h = box
    label = frame[y + int(h * 0.72): y + h, x: x + int(w * 0.75)]    # name sits bottom-left
    if label.size == 0:
        return ""
    import cv2
    label = cv2.resize(label, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)   # small text reads better
    result, _ = ocr(label)
    texts = [text for _, text, score in (result or []) if float(score) >= 0.6]
    return clean_name(texts)


def frames(video, every=SAMPLE_EVERY, width=WIDTH):
    """(seconds, BGR frame) from the video, one per `every` seconds."""
    import numpy as np
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "json", str(video)],
                           capture_output=True, text=True, check=True)
    stream = json.loads(probe.stdout)["streams"][0]
    height = int(stream["height"] * width / stream["width"]) // 2 * 2
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-threads", "2", "-i", str(video),
                             "-vf", f"fps=1/{every},scale={width}:{height}", "-f", "rawvideo",
                             "-pix_fmt", "bgr24", "pipe:"], stdout=subprocess.PIPE)
    size = width * height * 3
    index = 0
    try:
        while True:
            data = proc.stdout.read(size)
            if len(data) < size:
                break
            yield index * every, np.frombuffer(data, np.uint8).reshape(height, width, 3)
            index += 1
    finally:
        proc.kill()
        proc.wait()


def unify(names):
    """OCR spelling variants of one person -> the most common spelling."""
    counts = Counter(names)
    canonical = {}
    for name, _ in counts.most_common():
        match = next((c for c in set(canonical.values())
                      if SequenceMatcher(None, name.lower(), c.lower()).ratio() >= 0.8), None)
        canonical[name] = match or name
    return canonical


def timeline(samples, every=SAMPLE_EVERY, gap=2.0):
    """[(t, name or "")] -> merged [{"start", "end", "name"}], bridging short gaps."""
    canonical = unify([n for _, n in samples if n])
    spans = []
    for t, name in samples:
        if not name:
            continue
        name = canonical[name]
        if spans and spans[-1]["name"] == name and t - spans[-1]["end"] <= gap:
            spans[-1]["end"] = t + every
        else:
            spans.append({"start": t, "end": t + every, "name": name})
    return spans


def detect(video, report=lambda t: None, ocr=None):
    if ocr is None:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()
    samples, cache = [], {}          # cache: rough tile position -> (name, read at)
    for t, frame in frames(video):
        boxes = highlight_boxes(frame)
        name = ""
        if boxes:
            box = boxes[0]
            key = tuple(v // 24 for v in box)
            known = cache.get(key)
            if known and t - known[1] < REREAD_EVERY:
                name = known[0]
            else:
                name = read_name(frame, box, ocr)
                cache[key] = (name, t)
        samples.append((t, name))
        report(t)
    return timeline(samples)


def name_segments(segments, spans, offset=0.0, min_share=0.3):
    """Give each "Others" transcript segment the name of whoever Teams highlighted for most
    of it. offset: seconds from the start of the audio to the start of the screen video."""
    named = []
    for seg in segments:
        seg = dict(seg)
        if seg["speaker"] == "Others":
            length = max(0.01, seg["end"] - seg["start"])
            overlap = Counter()
            for span in spans:
                start, end = span["start"] + offset, span["end"] + offset
                shared = min(seg["end"], end) - max(seg["start"], start)
                if shared > 0:
                    overlap[span["name"]] += shared
            if overlap:
                name, shared = overlap.most_common(1)[0]
                if shared / length >= min_share:
                    seg["name"] = name
        named.append(seg)
    return named


def main(folder):
    folder = Path(folder)
    video = folder / "screen.mkv"
    spans = detect(video, lambda t: print(json.dumps({"seconds": t}), flush=True))
    (folder / "speakers.json").write_text(json.dumps(spans, ensure_ascii=False, indent=0))


if __name__ == "__main__":
    main(sys.argv[1])
