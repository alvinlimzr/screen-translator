"""
OCR lines -> tracked chunks with context, plus a background translation queue.
Pure Python (no macOS), so it is tested anywhere; overlay.py drives it.
"""
from __future__ import annotations

import heapq
import itertools
import math
import re
import threading
import traceback
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from chunker import Line, group_paragraphs, merge_touching, split_sentences
from langtools import extract_source
from tracker import Block, Label, Tracker, tracker_drop, tracker_shift

MAX_CONTEXT = 400
# lines like  " 1 "  ,  2/3  ,  !!  ,  ...  - only numbers / punctuation
GLUE = re.compile(r"^[\s\d๐-๙\"'“”„«»「」『』()\[\].,!?:;~\-–—…%฿$€¥/、。！？]+$")


def _center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def neighbour_texts(boxes_texts: List[Tuple[tuple, str]], i: int, k: int = 3) -> List[str]:
    """Texts of the k chunks closest to chunk i (reading order kept)."""
    cx, cy = _center(boxes_texts[i][0])
    others = [(math.hypot(_center(b)[0] - cx, _center(b)[1] - cy), j)
              for j, (b, _t) in enumerate(boxes_texts) if j != i]
    picked = sorted(j for _d, j in sorted(others)[:k])
    return [boxes_texts[j][1] for j in picked]


def sentence_context(sentences: List[str], idx: int, neighbours: List[str]) -> Optional[str]:
    """Context for one sentence: the rest of its paragraph + nearby text on screen."""
    parts = []
    if len(sentences) > 1:
        parts.append(" ".join(sentences))
    parts.extend(neighbours)
    ctx = "\n".join(parts).strip()
    if len(ctx) > MAX_CONTEXT:
        ctx = ctx[:MAX_CONTEXT]
    return ctx or None


class Pipeline:
    def __init__(self, lang: str, detect: Optional[Callable] = None, max_chunks: int = 40):
        self.lang, self.detect, self.max_chunks = lang, detect, max_chunks
        self.tracker = Tracker(max_dist=600, jitter_px=2, grace=1)   # units: points
        self.lock = threading.Lock()      # live tracking moves chunks from another thread

    def shift(self, lid: int, dx: float, dy: float):
        with self.lock:
            tracker_shift(self.tracker, lid, dx, dy)

    def drop(self, lid: int):
        with self.lock:
            tracker_drop(self.tracker, lid)

    def blocks(self, ocr_lines, scale: Tuple[float, float]) -> List[Block]:
        """ocr_lines: [(text, (l, t, r, b) in pixels)] -> chunks in points."""
        sx, sy = scale
        lines = []
        for text, box in ocr_lines:
            src = extract_source(text, self.lang, self.detect)
            if src:
                lines.append(Line(src, box))
            elif len(text.strip()) <= 12 and GLUE.match(text.strip()):
                lines.append(Line(text.strip(), box, glue=True))
        paras = [p for p in merge_touching(group_paragraphs(lines))
                 if any(not l.glue for l in p.lines)][: self.max_chunks]
        bt = [(p.box, p.text) for p in paras]
        out = []
        for i, p in enumerate(paras):
            l, t, r, b = p.box
            sents = split_sentences(p.text, self.lang)
            neigh = neighbour_texts(bt, i)
            out.append(Block(p.text, (l / sx, t / sy, r / sx, b / sy), meta={
                "sentences": sents,
                "contexts": [sentence_context(sents, k, neigh) for k in range(len(sents))],
                "line_h": p.line_height / sy,
            }))
        return out

    def step(self, ocr_lines, scale) -> List[Label]:
        blocks = self.blocks(ocr_lines, scale)
        with self.lock:
            return self.tracker.update(blocks)


class TranslateQueue:
    """One background worker translating sentences, most urgent first.

    priority: lower = sooner. The overlay uses the chunk's y position, so the
    page translates top to bottom, and boost() jumps a hovered chunk ahead.
    Sentences no longer on screen are skipped. Results go into .cache and
    on_done(label_id, sentence) is called from the worker thread.
    """

    def __init__(self, translator, on_done: Callable[[int, str], None],
                 src: str = "th", tgt: str = "en", disk=None):
        self.translator, self.on_done = translator, on_done
        self.src, self.tgt, self.disk = src, tgt, disk
        self.model_id = getattr(translator, "key", "none")
        # translations from earlier sessions: pages you've seen before are instant
        self.cache: Dict[str, str] = disk.load(self.model_id, src, tgt) if disk else {}
        self._heap: list = []
        self._seq = itertools.count()
        self._cv = threading.Condition()
        self._wanted: set = set()
        self._best: Dict[str, float] = {}      # sentence -> best queued priority (dedupe)
        self._stop = False
        self._warm = False
        self.working = False
        self.loading = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def pending(self) -> int:
        return len(self._best)

    def set_wanted(self, sentences: Iterable[str]):
        with self._cv:
            self._wanted = set(sentences)

    def submit(self, sentence: str, context: Optional[str], priority: float, label_id: int):
        if sentence in self.cache:
            return
        with self._cv:
            self._wanted.add(sentence)
            if sentence in self._best and priority >= self._best[sentence]:
                return                          # already queued at least this urgently
            self._best[sentence] = priority
            heapq.heappush(self._heap, (priority, next(self._seq), sentence, context, label_id))
            self._cv.notify()

    def submit_label(self, label: Label, priority: float):
        for s, c in zip(label.meta.get("sentences", []), label.meta.get("contexts", [])):
            self.submit(s, c, priority, label.id)

    def warmup(self):
        """Load the model now (on the worker thread) so the first translation is quick."""
        with self._cv:
            self._warm = True
            self._cv.notify()

    def stop(self):
        with self._cv:
            self._stop = True
            self._cv.notify()

    def _run(self):
        while True:
            with self._cv:
                while not self._heap and not self._stop and not self._warm:
                    self._cv.wait()
                if self._stop:
                    return
                warm, self._warm = self._warm, False
            if warm:
                if not getattr(self.translator, "loaded", True) and hasattr(self.translator, "_load"):
                    self.loading = True
                    try:
                        self.translator._load()
                    except Exception:
                        traceback.print_exc()
                    finally:
                        self.loading = False
                    try:
                        self.on_done(-1, "")          # tell the UI loading finished
                    except Exception:
                        traceback.print_exc()
                continue
            with self._cv:
                if not self._heap:
                    continue
                _p, _n, s, ctx, lid = heapq.heappop(self._heap)
                self._best.pop(s, None)
                if s in self.cache or s not in self._wanted:
                    continue
            self.working = True
            try:
                en = self.translator.translate(s, ctx, self.src, self.tgt)
                if self.disk is not None and en:
                    self.disk.put(self.model_id, self.src, self.tgt, s, en)
            except Exception as e:
                traceback.print_exc()
                en = f"(translation failed: {e})"
            finally:
                self.working = False
            if self._stop:
                return
            self.cache[s] = en
            try:
                self.on_done(lid, s)
            except Exception:
                traceback.print_exc()
