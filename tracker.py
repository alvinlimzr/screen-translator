"""
Label tracking across OCR cycles (pure Python, no macOS dependencies).

Each cycle (e.g. every 2s) you feed Tracker.update() the text blocks OCR found.
It matches them to the labels already on screen and tells the overlay what to do:

    new      -> fade in at .box
    same     -> leave alone
    moved    -> glide from .prev_box to .box   (see ease_box)
    missing  -> not found this cycle, keep showing at .box (grace period)
    removed  -> gone for good, fade out, then forget it

Boxes are pixels, top-left origin: (left, top, right, bottom).
"""
from __future__ import annotations

import difflib
import math
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

Box = Tuple[float, float, float, float]


@dataclass
class Block:
    text: str
    box: Box
    meta: dict = field(default_factory=dict)   # anything the caller wants carried along


@dataclass
class Label:
    id: int
    text: str
    box: Box
    prev_box: Optional[Box] = None
    status: str = "new"  # new | same | moved | missing | removed
    missed: int = 0
    translation: Optional[object] = None
    meta: dict = field(default_factory=dict)


def normalize(text: str) -> str:
    """Comparison key: NFKC (full-width -> ASCII), case-folded, with whitespace,
    punctuation, symbols and control chars removed. Letters and combining marks
    (e.g. Thai vowels) are kept."""
    t = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in t if unicodedata.category(ch)[0] not in "PSZC")


def _center(b: Box) -> Tuple[float, float]:
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _dist(a: Box, b: Box) -> float:
    (ax, ay), (bx, by) = _center(a), _center(b)
    return math.hypot(ax - bx, ay - by)


def ease_box(a: Box, b: Box, t: float) -> Box:
    """Smoothstep interpolation between two boxes, t in [0, 1]."""
    t = max(0.0, min(1.0, t))
    s = t * t * (3 - 2 * t)
    return tuple(x + (y - x) * s for x, y in zip(a, b))  # type: ignore


def blocks_from_ocrmac(results, width: int, height: int) -> List[Block]:
    """Convert ocrmac results [(text, conf, (x, y, w, h))] - normalized, origin
    bottom-left - into pixel Blocks with a top-left origin."""
    out = []
    for text, _conf, (x, y, w, h) in results:
        out.append(Block(text, (x * width, (1 - y - h) * height,
                                (x + w) * width, (1 - y) * height)))
    return out


class Tracker:
    def __init__(self, max_dist: float = 900, grace: int = 1,
                 jitter_px: float = 4, fuzzy_threshold: float = 0.8,
                 fuzzy_min_len: int = 6):
        self.max_dist = max_dist          # furthest a label may travel between cycles
        self.grace = grace                # missed cycles tolerated before removal
        self.jitter_px = jitter_px        # smaller shifts are treated as "same"
        self.fuzzy_threshold = fuzzy_threshold
        self.fuzzy_min_len = fuzzy_min_len
        self.labels: List[Label] = []
        self._next_id = 1

    def update(self, blocks: List[Block]) -> List[Label]:
        labels = self.labels
        kl = [normalize(l.text) for l in labels]
        kb = [normalize(b.text) for b in blocks]
        used_l, used_b = set(), set()
        match: Dict[int, int] = {}

        # Pass 1: identical text, nearest first (stops duplicates swapping).
        pairs = []
        for i, l in enumerate(labels):
            for j, b in enumerate(blocks):
                if kl[i] and kl[i] == kb[j]:
                    d = _dist(l.box, b.box)
                    if d <= self.max_dist:
                        pairs.append((d, i, j))
        for _d, i, j in sorted(pairs):
            if i not in used_l and j not in used_b:
                match[i] = j
                used_l.add(i)
                used_b.add(j)

        # Pass 2: near-identical text (OCR wobble such as o <-> 0).
        pairs = []
        for i, l in enumerate(labels):
            if i in used_l or len(kl[i]) < self.fuzzy_min_len:
                continue
            for j, b in enumerate(blocks):
                if j in used_b or len(kb[j]) < self.fuzzy_min_len:
                    continue
                d = _dist(l.box, b.box)
                if d > self.max_dist:
                    continue
                sm = difflib.SequenceMatcher(None, kl[i], kb[j])
                t = self.fuzzy_threshold
                if sm.real_quick_ratio() >= t and sm.quick_ratio() >= t:
                    r = sm.ratio()
                    if r >= t:
                        pairs.append((-r, d, i, j))
        for _r, _d, i, j in sorted(pairs):
            if i not in used_l and j not in used_b:
                match[i] = j
                used_l.add(i)
                used_b.add(j)

        result: List[Label] = []
        for i, l in enumerate(labels):
            if i in match:
                b = blocks[match[i]]
                l.missed = 0
                l.meta = b.meta
                if kl[i] != kb[match[i]]:      # text really changed -> retranslate
                    l.text = b.text
                    l.translation = None
                if _dist(l.box, b.box) < self.jitter_px:
                    l.status, l.prev_box = "same", None
                else:
                    l.status, l.prev_box, l.box = "moved", l.box, b.box
            else:
                l.missed += 1
                l.prev_box = None
                l.status = "removed" if l.missed > self.grace else "missing"
            result.append(l)

        for j, b in enumerate(blocks):
            if j not in used_b:
                result.append(Label(self._next_id, b.text, b.box, meta=b.meta))
                self._next_id += 1

        self.labels = [l for l in result if l.status != "removed"]
        return result


def _shift(box, dx, dy):
    return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


def tracker_shift(tracker: "Tracker", lid: int, dx: float, dy: float) -> None:
    for l in tracker.labels:
        if l.id == lid:
            l.box = _shift(l.box, dx, dy)


def tracker_drop(tracker: "Tracker", lid: int) -> None:
    tracker.labels = [l for l in tracker.labels if l.id != lid]


class TranslationCache:
    """Translate each distinct text once; moved labels never re-translate."""

    def __init__(self, translate_fn: Callable[[str], str]):
        self.fn = translate_fn
        self._cache: Dict[str, str] = {}

    def get(self, text: str) -> str:
        k = normalize(text)
        if k not in self._cache:
            self._cache[k] = self.fn(text)
        return self._cache[k]

    def fill(self, labels: List[Label]) -> None:
        for l in labels:
            if l.translation is None and l.status != "removed":
                l.translation = self.get(l.text)
