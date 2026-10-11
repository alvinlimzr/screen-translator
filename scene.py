"""
What's on screen right now: tracked chunks and their state (pure Python).
Used by the Windows app (and testable anywhere). Mutated only on the UI thread.

Chunk state:
  missing  the last OCR scan didn't find it (hidden; re-checked soon)
  stale    its area of the screen changed since the last scan (hidden)
Only "fresh" chunks (neither) are drawn or merged into cards.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import motion
from layout import group_overlapping

Box = Tuple[float, float, float, float]


def union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


class Scene:
    def __init__(self, width: float, height: float):
        self.w, self.h = width, height
        self.labels: Dict[int, dict] = {}       # id -> {"box", "meta", "missing", "stale"}
        self.live_boxes: Dict[int, Box] = {}    # read by the live-tracking thread
        self.norm_boxes: Dict[int, Box] = {}    # read by the scan thread (screen fractions)

    # ---------------------------------------------------------------- queries
    def fresh(self) -> List[int]:
        return [k for k, i in self.labels.items() if not i["missing"] and not i["stale"]]

    def groups(self, gap: float = 4) -> List[tuple]:
        return group_overlapping({k: self.labels[k]["box"] for k in self.fresh()}, gap=gap)

    def group_box(self, key) -> Box:
        return union([self.labels[k]["box"] for k in key if k in self.labels])

    def pairs_for(self, key, cache: dict):
        """[(sentence, translation or None)] and [(chunk id, sentence index)] for a group."""
        pairs, owners = [], []
        for k in key:
            info = self.labels.get(k)
            if not info:
                continue
            for i, s in enumerate(info["meta"].get("sentences", [])):
                pairs.append((s, cache.get(s)))
                owners.append((k, i))
        return pairs, owners

    def chunk_at(self, x: float, y: float, slop: float = 8):
        for k in self.fresh():
            l, t, r, b = self.labels[k]["box"]
            if l - slop <= x <= r + slop and t - slop <= y <= b + slop:
                return k
        return None

    # ---------------------------------------------------------------- updates
    def sync(self):
        """Publish boxes to background threads (plain dict swaps are atomic)."""
        self.live_boxes = {k: i["box"] for k, i in self.labels.items() if not i["missing"]}
        self.norm_boxes = {k: (b[0] / self.w, b[1] / self.h, b[2] / self.w, b[3] / self.h)
                           for k, b in self.live_boxes.items()}

    def clear(self):
        self.labels.clear()
        self.sync()

    def apply_scan(self, snap) -> bool:
        """snap: [(id, status, box, meta)] from the tracker. Returns True if any chunk
        went missing (the caller should re-check soon)."""
        any_missing = False
        for k, status, box, meta in snap:
            if status == "removed":
                self.labels.pop(k, None)
                continue
            missing = status == "missing"
            any_missing |= missing
            old = self.labels.get(k)
            self.labels[k] = {"box": old["box"] if (missing and old) else box,
                              "meta": meta, "missing": missing, "stale": False}
        self.sync()
        return any_missing

    def set_stale(self, ids) -> bool:
        changed = False
        for k, info in self.labels.items():
            st = k in ids
            if info["stale"] != st:
                info["stale"] = st
                changed = True
        return changed

    def apply_motion(self, moves, pipe=None):
        """moves: {id: (dx, dy, confidence, content_changed)} in points.
        Returns (deltas {id: (dx, dy)}, membership_changed)."""
        deltas, membership = {}, False
        for k, (dx, dy, _conf, changed) in moves.items():
            info = self.labels.get(k)
            if info is None or info["missing"]:
                continue
            if changed:
                if not info["stale"]:
                    info["stale"] = True
                    membership = True
                continue
            if abs(dx) < 0.3 and abs(dy) < 0.3:
                continue
            l, t, r, b = info["box"]
            info["box"] = (l + dx, t + dy, r + dx, b + dy)
            if pipe is not None:
                pipe.shift(k, dx, dy)
            deltas[k] = (dx, dy)
            if motion.offscreen(info["box"], (self.w, self.h)):
                self.labels.pop(k)
                if pipe is not None:
                    pipe.drop(k)
                membership = True
        self.sync()
        return deltas, membership
