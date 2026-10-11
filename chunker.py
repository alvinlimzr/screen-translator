"""
Turn OCR lines into neat translation chunks (pure Python + PIL).

  lines -> paragraphs (lines stacked close together, similar height, aligned)
        -> paragraph text (Thai/CJK lines joined without spaces)
        -> sentences (split for better translation; each can be saved separately)

Boxes are (left, top, right, bottom), top-left origin, any unit.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import List, Tuple

Box = Tuple[float, float, float, float]

NO_SPACE_SCRIPT = re.compile(r"[฀-๿぀-ヿ㐀-䶿一-鿿]")


@dataclass
class Line:
    text: str
    box: Box
    glue: bool = False        # numbers/quotes only: may join a chunk, never forms one


@dataclass
class Paragraph:
    lines: List[Line] = field(default_factory=list)

    @property
    def box(self) -> Box:
        return (min(l.box[0] for l in self.lines), min(l.box[1] for l in self.lines),
                max(l.box[2] for l in self.lines), max(l.box[3] for l in self.lines))

    @property
    def line_height(self) -> float:
        return statistics.median(l.box[3] - l.box[1] for l in self.lines)

    @property
    def text(self) -> str:
        return join_lines([l.text for l in self.lines])


def _h(b: Box) -> float:
    return max(1e-6, b[3] - b[1])


def _x_overlap(a: Box, b: Box) -> float:
    """Overlap as a fraction of the narrower box."""
    inter = min(a[2], b[2]) - max(a[0], b[0])
    return max(0.0, inter) / max(1e-6, min(a[2] - a[0], b[2] - b[0]))


def group_paragraphs(lines: List[Line], max_gap: float = 0.9, max_height_ratio: float = 1.25,
                     align_tol: float = 1.5, min_overlap: float = 0.3) -> List[Paragraph]:
    """Greedy top-to-bottom grouping. A line joins the paragraph whose last line
    sits just above it (gap <= max_gap * line height), has a similar height, and is
    left-aligned with it or overlaps it horizontally."""
    paras: List[Paragraph] = []
    for line in sorted(lines, key=lambda l: (l.box[1], l.box[0])):
        best, best_gap = None, None
        for p in paras:
            last = p.lines[-1]
            h = _h(last.box)
            small = min(h, _h(line.box))
            gap = line.box[1] - last.box[3]
            if gap < -0.3 * small or gap > max_gap * small:
                continue
            ratio = max(h, _h(line.box)) / small
            if ratio > max_height_ratio:
                continue
            aligned = abs(line.box[0] - last.box[0]) <= align_tol * h
            if not aligned and _x_overlap(line.box, last.box) < min_overlap:
                continue
            if best is None or gap < best_gap:
                best, best_gap = p, gap
        if best is None:
            paras.append(Paragraph([line]))
        else:
            best.lines.append(line)
    return paras


def _touch(a: Box, b: Box, pad: float) -> bool:
    return (a[0] - pad < b[2] and b[0] - pad < a[2] and
            a[1] - pad < b[3] and b[1] - pad < a[3])


def merge_touching(paras: List[Paragraph], touch: float = 0.5,
                   max_lines: int = 14) -> List[Paragraph]:
    """Merge paragraphs whose boxes overlap or nearly touch (within `touch` x line
    height). Catches lines the stricter grouping kept apart: handwriting, uneven
    sizes, centred text, speech bubbles. Separate web paragraphs (gap ~1 line)
    stay separate."""
    paras = [Paragraph(list(p.lines)) for p in paras]
    merged = True
    while merged:
        merged = False
        for i in range(len(paras)):
            for j in range(i + 1, len(paras)):
                a, b = paras[i], paras[j]
                if len(a.lines) + len(b.lines) > max_lines:
                    continue
                pad = touch * min(a.line_height, b.line_height)
                if _touch(a.box, b.box, pad):
                    lines = sorted(a.lines + b.lines, key=lambda l: (l.box[1], l.box[0]))
                    paras[i] = Paragraph(lines)
                    del paras[j]
                    merged = True
                    break
            if merged:
                break
    return sorted(paras, key=lambda p: (p.box[1], p.box[0]))


def join_lines(texts: List[str]) -> str:
    out = ""
    for t in (t.strip() for t in texts):
        if not t:
            continue
        if not out:
            out = t
        elif NO_SPACE_SCRIPT.match(out[-1]) and NO_SPACE_SCRIPT.match(t[0]):
            out += t                      # Thai/CJK wrap: no space between lines
        else:
            out += " " + t
    return out


_CJK_END = re.compile(r"(?<=[。！？!?])")
_LATIN_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str, lang: str, min_chars: int = 20, max_chars: int = 160) -> List[str]:
    """Split into translation-sized pieces.
    Thai has no full stop and spaces are also used between phrases, so a chunk is
    only cut (at spaces) when it is longer than max_chars: a speech bubble or a
    short paragraph is translated as one unit, with all its meaning together.
    Japanese / English split at sentence punctuation; very short pieces are merged."""
    text = text.strip()
    if not text:
        return []
    if lang == "th":
        if len(text) <= max_chars:
            return [text]
        out, cur = [], ""
        for p in text.split():
            if cur and len(cur) + 1 + len(p) > max_chars:
                out.append(cur)
                cur = p
            else:
                cur = (cur + " " + p).strip()
        if cur:
            out.append(cur)
        if len(out) > 1 and len(out[-1]) < min_chars:
            tail = out.pop()
            out[-1] = out[-1] + " " + tail
        return out
    if lang in ("ja", "zh"):
        pieces = [p for p in _CJK_END.split(text) if p.strip()]
        sep = ""
    else:
        pieces = [p for p in _LATIN_END.split(text) if p.strip()]
        sep = " "

    out: List[str] = []
    cur = ""
    for p in pieces:
        p = p.strip()
        if not cur:
            cur = p
        elif len(cur) < min_chars or len(p) < min_chars // 2:
            if len(cur) + len(sep) + len(p) <= max_chars:
                cur = cur + sep + p
            else:
                out.append(cur)
                cur = p
        else:
            out.append(cur)
            cur = p
    if cur:
        out.append(cur)
    if len(out) > 1 and len(out[-1]) < min_chars // 2:
        tail = out.pop()
        out[-1] = out[-1] + sep + tail
    return out


def sample_background(img, box: Box, margin: int = 3) -> Tuple[int, int, int]:
    """Median colour of a thin ring just outside `box` (pixels) in a PIL RGB image."""
    W, H = img.size
    l, t, r, b = (int(round(v)) for v in box)
    px = img.load()
    samples = []
    step = max(1, (r - l) // 40)
    for x in range(max(0, l), min(W, r), step):
        for y in (t - margin, b + margin):
            if 0 <= y < H:
                samples.append(px[x, y][:3])
    vstep = max(1, (b - t) // 10)
    for y in range(max(0, t), min(H, b), vstep):
        for x in (l - margin, r + margin):
            if 0 <= x < W:
                samples.append(px[x, y][:3])
    if not samples:
        return (255, 255, 255)
    return tuple(int(statistics.median(s[i] for s in samples)) for i in range(3))


def text_color_for(bg: Tuple[int, int, int]) -> Tuple[int, int, int]:
    r, g, b = (c / 255 for c in bg)
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (20, 20, 20) if lum > 0.55 else (245, 245, 245)
