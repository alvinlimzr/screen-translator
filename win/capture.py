"""Screen capture on Windows with `mss` (GDI). Our overlay window is excluded
from captures with SetWindowDisplayAffinity, so it never reads its own cards."""
from __future__ import annotations

import threading

import numpy as np


class MssCapture:
    def __init__(self, logical_size, monitor_index=None):
        """logical_size: primary screen size in Qt points (device-independent)."""
        self.lw, self.lh = logical_size
        self.excluded = False            # set True once the self-test confirms exclusion
        self._local = threading.local()
        self.monitor_index = monitor_index

    def _sct(self):
        sct = getattr(self._local, "sct", None)
        if sct is None:
            import mss
            sct = self._local.sct = mss.mss()
        return sct

    def _monitor(self, sct):
        if self.monitor_index is not None:
            return sct.monitors[self.monitor_index]
        # primary monitor = the one at (0, 0)
        for m in sct.monitors[1:]:
            if m["left"] == 0 and m["top"] == 0:
                return m
        return sct.monitors[1]

    def native_scale(self) -> float:
        m = self._monitor(self._sct())
        return m["width"] / self.lw

    def grab(self, px_per_pt: float = 0.0):
        """-> (BGRA image as numpy array, pixels per point). px_per_pt 0 = native."""
        sct = self._sct()
        m = self._monitor(sct)
        shot = sct.grab(m)
        img = np.frombuffer(shot.raw, dtype=np.uint8).reshape(shot.height, shot.width, 4)
        native = shot.width / self.lw
        if not px_per_pt or px_per_pt >= native * 0.95:
            return img, native
        step = native / px_per_pt
        if step >= 1.9:                                     # fast integer subsample
            k = int(step)
            return img[::k, ::k], native / k
        try:
            import cv2
            w, h = int(shot.width / step), int(shot.height / step)
            return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA), native / step
        except ImportError:
            return img, native

    def pixel(self, x_pt: float, y_pt: float):
        img, ppp = self.grab(0)
        b, g, r, _a = img[int(y_pt * ppp), int(x_pt * ppp)]
        return int(r), int(g), int(b)
