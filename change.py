"""
Decide when to run OCR (pure Python).

Every check (~0.4 s) the app grabs a tiny greyscale thumbnail of the screen.
OCR only runs when the screen has changed since the last scan, the minimum
interval has passed, and the screen has stopped moving (or has kept moving for
longer than max_wait, e.g. a video), so an idle screen costs almost nothing and
text is read as soon as you stop scrolling.
"""
from __future__ import annotations

from typing import Optional


def diff_fraction(a: bytes, b: bytes, tol: int = 12) -> float:
    """Fraction of thumbnail pixels that changed by more than `tol` grey levels."""
    if a is None or b is None or len(a) != len(b):
        return 1.0
    n = sum(1 for x, y in zip(a, b) if abs(x - y) > tol)
    return n / max(1, len(a))


class ChangeGate:
    def __init__(self, min_interval: float = 1.0, max_wait: float = 1.5, threshold: float = 0.002):
        self.min_interval, self.max_wait, self.threshold = min_interval, max_wait, threshold
        self.last_scanned: Optional[bytes] = None
        self.last_scan_time = -1e9
        self.prev: Optional[bytes] = None
        self.dirty_since: Optional[float] = None
        self.force = False

    def should_scan(self, thumb: bytes, now: float) -> bool:
        moving = self.prev is not None and diff_fraction(thumb, self.prev) > self.threshold
        self.prev = thumb
        changed = self.force or self.last_scanned is None or \
            diff_fraction(thumb, self.last_scanned) > self.threshold
        if not changed:
            self.dirty_since = None
            return False
        if self.dirty_since is None:
            self.dirty_since = now
        if now - self.last_scan_time < self.min_interval:
            return False
        if moving and not self.force and now - self.dirty_since < self.max_wait:
            return False                       # still scrolling: wait for it to settle
        self.last_scanned, self.last_scan_time = thumb, now
        self.dirty_since, self.force = None, False
        return True
