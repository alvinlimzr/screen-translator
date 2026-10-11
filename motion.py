"""
Motion tracking between screen frames (pure numpy, testable anywhere).

Live tracking mode grabs a small greyscale frame ~12 times a second. For each
translated chunk, phase correlation between the previous and current frame,
in a window around the chunk, says how far its content moved. Each chunk is
tracked on its own, so two panes scrolling differently (or a static sidebar)
are handled. If the content under a chunk changed instead of moving
(new page, closed tab), the chunk is reported as `changed`.

Also here: `stale_ids()` for standard mode, which checks which chunks' screen
regions changed since the last OCR scan, using the cheap change-detection
thumbnail.
"""
from __future__ import annotations

from typing import Dict, Iterable, Tuple

import numpy as np

Box = Tuple[float, float, float, float]


_SMOOTH = sorted({2 ** a * 3 ** b * 5 ** c for a in range(11) for b in range(7) for c in range(5)
                  if 8 <= 2 ** a * 3 ** b * 5 ** c <= 1024})
_WIN = {}


def smooth_floor(n: int) -> int:
    """Largest size <= n made only of factors 2, 3, 5 (fast for FFT)."""
    best = 8
    for v in _SMOOTH:
        if v > n:
            break
        best = v
    return best


def _window(h: int, w: int) -> np.ndarray:
    k = (h, w)
    if k not in _WIN:
        _WIN[k] = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    return _WIN[k]


def frame_from_bytes(data: bytes, w: int, h: int) -> np.ndarray:
    return np.frombuffer(data, dtype=np.uint8)[: w * h].reshape(h, w).astype(np.float32)


def phase_shift(a: np.ndarray, b: np.ndarray) -> Tuple[float, float, float]:
    """Displacement (dy, dx) that moves content of `a` onto `b`, plus peak strength
    (~1 for a clean shift, ~0 for unrelated patches). Sub-pixel by parabola fit."""
    h, w = a.shape
    if h < 4 or w < 4:
        return 0.0, 0.0, 0.0
    win = _window(h, w)
    fa = np.fft.rfft2((a - a.mean()) * win)
    fb = np.fft.rfft2((b - b.mean()) * win)
    r = fb * np.conj(fa)
    r /= np.abs(r) + 1e-6
    c = np.fft.irfft2(r, s=(h, w))
    iy, ix = np.unravel_index(int(np.argmax(c)), c.shape)
    peak = float(c[iy, ix])

    def sub(cm, c0, cp):
        d = cm - 2 * c0 + cp
        return 0.0 if abs(d) < 1e-9 else 0.5 * (cm - cp) / d

    fy = sub(c[(iy - 1) % h, ix], c[iy, ix], c[(iy + 1) % h, ix])
    fx = sub(c[iy, (ix - 1) % w], c[iy, ix], c[iy, (ix + 1) % w])
    dy = iy + fy
    dx = ix + fx
    if dy > h / 2:
        dy -= h
    if dx > w / 2:
        dx -= w
    return float(dy), float(dx), peak


def track(prev: np.ndarray, cur: np.ndarray, boxes: Dict[int, Box],
          margin: Tuple[int, int] = (56, 24), min_peak: float = 0.12,
          change_tol: float = 18.0, max_half: Tuple[int, int] = (40, 110)) -> Dict[int, Tuple[float, float, float, bool]]:
    """boxes in frame pixels -> {id: (dx, dy, confidence, changed)}.
    `changed` = the content under the box no longer matches, even after shifting."""
    H, W = prev.shape
    my, mx = margin
    out = {}
    if prev.shape != cur.shape:
        return out
    for key, (l, t, r, b) in boxes.items():
        # big paragraphs: a central slice is enough to measure motion (and much faster)
        cx, cy = (l + r) / 2, (t + b) / 2
        hw, hh = min((r - l) / 2, max_half[1]), min((b - t) / 2, max_half[0])
        L, T = max(0, int(cx - hw) - mx), max(0, int(cy - hh) - my)
        R, B = min(W, int(cx + hw) + mx), min(H, int(cy + hh) + my)
        if R - L < 8 or B - T < 8:
            continue
        # trim to FFT-friendly sizes (sizes with big prime factors are several x slower)
        th, tw = smooth_floor(B - T), smooth_floor(R - L)
        T += (B - T - th) // 2
        L += (R - L - tw) // 2
        B, R = T + th, L + tw
        pa, pb = prev[T:B, L:R], cur[T:B, L:R]
        if float(np.abs(pa - pb).mean()) < 1.0:
            out[key] = (0.0, 0.0, 1.0, False)            # nothing moved here
            continue
        dy, dx, peak = phase_shift(pa, pb)
        ok = peak >= min_peak
        # verify: compare the box's content now vs before, at the found offset
        changed = False
        sy, sx = int(round(dy)) if ok else 0, int(round(dx)) if ok else 0
        bl, bt, br, bb = int(l), int(t), int(r), int(b)
        ol, ot, orr, ob = bl + sx, bt + sy, br + sx, bb + sy
        if 0 <= ol and 0 <= ot and orr <= W and ob <= H and br > bl and bb > bt \
                and bl >= 0 and bt >= 0 and br <= W and bb <= H:
            diff = float(np.abs(prev[bt:bb, bl:br] - cur[ot:ob, ol:orr]).mean())
            changed = diff > change_tol
        out[key] = (dx if ok else 0.0, dy if ok else 0.0, peak, changed)
    return out


def offscreen(box: Box, screen: Tuple[float, float], keep: float = 0.4) -> bool:
    """True when less than `keep` of the box is still on screen."""
    l, t, r, b = box
    sw, sh = screen
    area = max(1e-6, (r - l) * (b - t))
    vis = max(0.0, min(r, sw) - max(l, 0.0)) * max(0.0, min(b, sh) - max(t, 0.0))
    return vis / area < keep


def stale_ids(now: bytes, ref: bytes, tw: int, th: int, boxes_norm: Dict[int, Box],
              tol: int = 14, frac: float = 0.2) -> set:
    """Standard mode: chunks whose region of the thumbnail changed since the last
    OCR scan (scrolled, page switched...). boxes_norm are 0..1 screen fractions."""
    if ref is None or len(now) != len(ref):
        return set()
    a = np.frombuffer(now, dtype=np.uint8)[: tw * th].reshape(th, tw).astype(np.int16)
    b = np.frombuffer(ref, dtype=np.uint8)[: tw * th].reshape(th, tw).astype(np.int16)
    moved = np.abs(a - b) > tol
    out = set()
    for key, (l, t, r, bt) in boxes_norm.items():
        L, T = max(0, int(l * tw)), max(0, int(t * th))
        R, B = min(tw, max(L + 1, int(np.ceil(r * tw)))), min(th, max(T + 1, int(np.ceil(bt * th))))
        cell = moved[T:B, L:R]
        if cell.size and cell.mean() > frac:
            out.add(key)
    return out


def shift_boxes(boxes: Iterable[Box], dx: float, dy: float):
    return [(l + dx, t + dy, r + dx, b + dy) for l, t, r, b in boxes]
