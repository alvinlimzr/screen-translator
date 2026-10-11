"""
Card layout for "translate everything" mode (pure Python, testable anywhere).

Each chunk gets a card over its text. If the English doesn't fit:
  1. shrink the font (down to min_pt)
  2. widen the card (up to ~1.6x, staying on screen)
  3. only then grow taller
If two cards would then overlap, their chunks are drawn as ONE combined card
over both areas, and the layout is redone until nothing overlaps.

`measure(pairs, pt, width) -> height` is supplied by the caller, so the Mac app
measures with the exact text engine it draws with (and tests use PIL).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Callable, List, Sequence, Tuple

Box = Tuple[float, float, float, float]
Frame = Tuple[float, float, float, float]          # x, y, w, h


@dataclass
class Item:
    key: int                  # chunk id
    box: Box                  # source text area (points)
    line_h: float
    pairs: List[tuple]        # [(source sentence, english or None)]


@dataclass
class Card:
    keys: Tuple[int, ...]
    frame: Frame
    text_frame: Frame         # relative to the card
    pt: float
    pairs: List[tuple]
    owners: List[Tuple[int, int]] = field(default_factory=list)   # pair index -> (key, sentence idx)


def _union(boxes: Sequence[Box]) -> Box:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _overlap(a: Frame, b: Frame, gap: float) -> bool:
    return (a[0] < b[0] + b[2] + gap and b[0] < a[0] + a[2] + gap and
            a[1] < b[1] + b[3] + gap and b[1] < a[1] + a[3] + gap)


def fit_card(items: Sequence[Item], measure: Callable, screen: Tuple[float, float],
             pad: float = 4, min_pt: float = 11, max_pt: float = 26,
             max_widen: float = 1.6, min_width: float = 220) -> Card:
    items = sorted(items, key=lambda it: (it.box[1], it.box[0]))
    box = _union([it.box for it in items])
    l, t, r, b = box
    bw, bh = max(40.0, r - l), b - t
    line_h = statistics.median(it.line_h for it in items)
    pairs, owners = [], []
    for it in items:
        for k, p in enumerate(it.pairs):
            pairs.append(p)
            owners.append((it.key, k))

    sw, sh = screen
    pt = max(min_pt, min(max_pt, round(line_h * 0.72)))
    width, h = bw, measure(pairs, pt, bw)
    while h > bh and pt > min_pt:                          # 1. smaller font
        pt -= 1
        h = measure(pairs, pt, width)
    if h > bh:                                             # 2. wider card
        max_w = min(max(bw * max_widen, min_width), sw - 2 * pad - 8)
        w = bw
        while h > bh and w < max_w:
            w = min(max_w, w + 24)
            h = measure(pairs, pt, w)
        width = w
    x = l - pad
    if x + width + 2 * pad > sw - 4:                       # keep on screen
        x = max(4, sw - 4 - width - 2 * pad)
    height = max(bh, h)                                    # 3. taller if still needed
    y = t - pad
    if y + height + 2 * pad > sh - 2:                      # keep on screen vertically
        y = sh - 2 - height - 2 * pad
    y = max(2, y)
    frame = (x, y, width + 2 * pad, height + 2 * pad)
    return Card(tuple(it.key for it in items), frame, (pad, pad, width, h + 2), pt, pairs, owners)


def plan(items: Sequence[Item], measure: Callable, screen: Tuple[float, float],
         gap: float = 3, **kw) -> List[Card]:
    groups: List[List[Item]] = [[it] for it in items]
    while True:
        cards = [fit_card(g, measure, screen, **kw) for g in groups]
        clash = next(((i, j) for i in range(len(cards)) for j in range(i + 1, len(cards))
                      if _overlap(cards[i].frame, cards[j].frame, gap)), None)
        if clash is None:
            return cards
        i, j = clash
        groups[i] = groups[i] + groups[j]
        del groups[j]


def group_overlapping(boxes: dict, gap: float = 2) -> List[Tuple[int, ...]]:
    """Ids whose boxes overlap (or nearly touch) end up in one group (hover mode
    draws one outline and one tooltip per group). Groups are in reading order."""
    keys = list(boxes)
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            A, B = boxes[a], boxes[b]
            if (A[0] - gap < B[2] and B[0] - gap < A[2] and A[1] - gap < B[3] and B[1] - gap < A[3]):
                parent[find(a)] = find(b)
    groups = {}
    for k in keys:
        groups.setdefault(find(k), []).append(k)
    out = [tuple(sorted(g, key=lambda k: (boxes[k][1], boxes[k][0]))) for g in groups.values()]
    return sorted(out, key=lambda g: (boxes[g[0]][1], boxes[g[0]][0]))
