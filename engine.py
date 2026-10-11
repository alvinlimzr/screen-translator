"""
The translation engine without any UI (pure Python + numpy), used by the
Windows app. Platform pieces are injected:

  capture.grab(px_per_pt) -> (BGR or BGRA uint8 image, px_per_pt actually used)
  capture.excluded        -> True if our overlay is left out of screenshots
  ocr.read(image, lang)   -> [(text, (l, t, r, b) in image pixels)]
  post(fn, *args)         -> run fn on the UI thread (Qt signal, or direct in tests)
  post_later(sec, fn)     -> same, after a delay

Background threads: a scan loop (cheap change check every 0.4 s, OCR only
when the screen changed and settled) and, in live tracking mode, a ~12 fps
motion loop. All chunk state lives in `self.scene`, changed on the UI thread
only, and listeners hear about every change.
"""
from __future__ import annotations

import threading
import time
import traceback

import numpy as np

import motion
from change import ChangeGate
from pipeline import Pipeline, TranslateQueue
from scene import Scene
from settings import OCR_SCALE

CHECK_EVERY = 0.4
LIVE_FPS = 12
LIVE_PX = 0.5
THUMB_W, THUMB_H = 160, 100


# ---------------------------------------------------------------- image helpers
def to_gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return img
    b, g, r = img[..., 0].astype(np.float32), img[..., 1].astype(np.float32), img[..., 2].astype(np.float32)
    return (0.114 * b + 0.587 * g + 0.299 * r).astype(np.uint8)


def resize_area(gray: np.ndarray, w: int, h: int) -> np.ndarray:
    """Average-pool resize (fast, no OpenCV needed)."""
    H, W = gray.shape
    ys = (np.arange(h + 1) * H // h)
    xs = (np.arange(w + 1) * W // w)
    c = np.cumsum(np.cumsum(np.pad(gray.astype(np.float64), ((1, 0), (1, 0))), 0), 1)
    tot = c[ys[1:]][:, xs[1:]] - c[ys[:-1]][:, xs[1:]] - c[ys[1:]][:, xs[:-1]] + c[ys[:-1]][:, xs[:-1]]
    area = np.outer(np.diff(ys), np.diff(xs)).clip(min=1)
    return (tot / area).astype(np.uint8)


def thumbnail(gray: np.ndarray) -> bytes:
    return resize_area(gray, THUMB_W, THUMB_H).tobytes()


# ---------------------------------------------------------------- engine
class Engine:
    def __init__(self, settings, translator, disk, capture, ocr, screen_size, post, post_later=None):
        self.s = settings
        self.translator, self.disk, self.capture, self.ocr = translator, disk, capture, ocr
        self.post = post
        self.post_later = post_later or (lambda sec, fn: None)
        self.scene = Scene(*screen_size)
        self.listeners = []                 # fn(kind, payload) on the UI thread
        self.paused = settings.launch_paused
        self.selecting = False
        self.gate = ChangeGate(min_interval=settings.scan_interval)
        self.ref_thumb = None
        self.motion_epoch = 0
        self.discards = 0
        self.live_fps = 0.0
        self.stats = {"ocr": None, "scans": 0, "skipped": 0}
        self.hide_overlay = None            # set by the UI if capture can't exclude it
        self._running = False
        self._make_pipeline()

    # ---------------------------------------------------------------- lifecycle
    def _make_pipeline(self):
        self.pipe = Pipeline(self.s.source, None, max_chunks=40)
        self.queue = TranslateQueue(self.translator, self._translated_from_worker,
                                    self.s.source, self.s.target, self.disk)
        self.queue.warmup()

    def start(self):
        self._running = True
        threading.Thread(target=self._scan_loop, daemon=True, name="scan").start()
        threading.Thread(target=self._live_loop, daemon=True, name="live").start()

    def stop(self):
        self._running = False
        self.queue.stop()

    def reset(self):
        """New language pair, model or area: forget what's on screen and rescan."""
        self.queue.stop()
        self.scene.clear()
        self._make_pipeline()
        self.ref_thumb = None
        self.gate.force = True
        self._emit("reset", None)

    def set_translator(self, translator):
        self.translator = translator
        self.reset()

    def settings_changed(self, names):
        if names & {"source", "target", "roi"}:
            self.reset()
        if names & {"scan_interval", "ocr_quality", "tracking"}:
            self.gate.min_interval = self.s.scan_interval
            self.gate.force = True
        if "mode" in names:
            self.submit_all()

    def set_paused(self, paused):
        self.paused = paused
        if not paused:
            self.gate.force = True
        self._emit("status", self.status())

    # ---------------------------------------------------------------- events
    def _emit(self, kind, payload):
        for fn in list(self.listeners):
            try:
                fn(kind, payload)
            except Exception:
                traceback.print_exc()

    def status(self):
        busy = self.queue.pending() > 0 or self.queue.working
        return {"paused": self.paused, "chunks": len(self.scene.fresh()),
                "queued": self.queue.pending() + (1 if self.queue.working else 0),
                "ocr": self.stats["ocr"], "scans": self.stats["scans"], "skipped": self.stats["skipped"],
                "cached": len(self.queue.cache),
                "capture": "sck" if self.capture.excluded else "hide",
                "live_fps": self.live_fps if self.s.tracking == "live" else None,
                "loading_model": self.queue.loading or (busy and not getattr(self.translator, "loaded", True))}

    # ---------------------------------------------------------------- scanning (background)
    def px_per_pt(self):
        return OCR_SCALE.get(self.s.ocr_quality) or 0.0     # 0 = native

    def _scan_loop(self):
        failures = 0
        while self._running:
            t0 = time.time()
            if not self.paused and not self.selecting:
                try:
                    if self.capture.excluded or self.gate.force or \
                            t0 - self.gate.last_scan_time >= self.s.scan_interval:
                        self.check_once()
                    failures = 0
                except Exception as e:
                    failures += 1
                    if failures <= 3:
                        traceback.print_exc()
                    if failures == 3:
                        self.post(self._emit, "status", {"note": f"Screen capture failed: {e}"})
                    time.sleep(min(10.0, 0.5 * failures))     # back off instead of spinning
            time.sleep(max(0.05, CHECK_EVERY - (time.time() - t0)))

    def check_once(self):
        """One iteration: capture, cheap change check, maybe OCR. Returns what happened."""
        epoch = self.motion_epoch
        hide = not self.capture.excluded and self.hide_overlay is not None
        if hide:
            self.hide_overlay(True)
        try:
            img, ppp = self.capture.grab(self.px_per_pt())
        finally:
            if hide:
                self.hide_overlay(False)
        gray = to_gray(img)
        thumb = thumbnail(gray)
        roi = self.s.roi
        if roi:
            l, t, r, b = roi
            ox, oy = int(l * ppp), int(t * ppp)
            crop = img[oy:int(b * ppp), ox:int(r * ppp)]
            gate_thumb = thumbnail(to_gray(crop)) if crop.size else thumb
        else:
            ox = oy = 0
            crop, gate_thumb = img, thumb
        now = time.time()

        if not self.capture.excluded:
            self.gate.last_scan_time, self.gate.force = now, False      # every capture is a scan
        elif not self.gate.should_scan(gate_thumb, now):
            if self.s.tracking != "live":
                stale = motion.stale_ids(thumb, self.ref_thumb, THUMB_W, THUMB_H, dict(self.scene.norm_boxes))
                self.post(self._ui_stale, stale)
            self.stats["skipped"] += 1
            return "skipped"

        t0 = time.perf_counter()
        found = self.ocr.read(crop, self.s.source) if crop.size else []
        self.stats["ocr"] = time.perf_counter() - t0
        self.stats["scans"] += 1
        lines = [(txt, (bl + ox, bt + oy, br + ox, bb + oy)) for txt, (bl, bt, br, bb) in found]
        if self.motion_epoch != epoch and self.discards < 2:
            self.discards += 1                  # text moved while we were reading it
            self.gate.force = True
            return "discarded"
        self.discards = 0
        pipe = self.pipe
        labels = pipe.step(lines, (ppp, ppp))
        snap = [(lab.id, lab.status, lab.box, lab.meta) for lab in labels]
        self.post(self._ui_scan, snap, pipe, thumb)
        return "scanned"

    # ---------------------------------------------------------------- live tracking (background)
    def _live_loop(self):
        prev = None
        while self._running:
            t0 = time.time()
            if (self.s.tracking != "live" or self.paused or self.selecting
                    or not self.capture.excluded or not self.scene.live_boxes):
                prev = None
                time.sleep(0.25)
                continue
            try:
                prev = self.live_once(prev)
            except Exception:
                traceback.print_exc()
                time.sleep(1.0)
            dt = time.time() - t0
            self.live_fps = 0.8 * self.live_fps + 0.2 * (1 / max(dt, 1 / LIVE_FPS))
            time.sleep(max(0.0, 1 / LIVE_FPS - dt))

    def live_once(self, prev):
        img, ppp = self.capture.grab(LIVE_PX)
        frame = to_gray(img).astype(np.float32)
        boxes = dict(self.scene.live_boxes)
        if prev is not None and prev.shape == frame.shape and boxes:
            res = motion.track(prev, frame, {k: (b[0] * ppp, b[1] * ppp, b[2] * ppp, b[3] * ppp)
                                            for k, b in boxes.items()})
            moves = {k: (dx / ppp, dy / ppp, conf, ch) for k, (dx, dy, conf, ch) in res.items()
                     if ch or abs(dx) > 0.3 or abs(dy) > 0.3}
            if moves:
                self.post(self._ui_motion, moves)
        return frame

    # ---------------------------------------------------------------- UI-thread handlers
    def _ui_scan(self, snap, pipe, thumb):
        if pipe is not self.pipe:               # language / area changed meanwhile
            return
        any_missing = self.scene.apply_scan(snap)
        self.ref_thumb = thumb
        self.submit_all()
        self._emit("scene", {"animated": True})
        if any_missing:                         # confirm disappearances soon
            self.post_later(0.7, self._force)
        self._emit("status", self.status())

    def _force(self):
        self.gate.force = True

    def _ui_stale(self, ids):
        if self.scene.set_stale(ids):
            self._emit("scene", {"animated": False})
        self._emit("status", self.status())

    def _ui_motion(self, moves):
        deltas, membership = self.scene.apply_motion(moves, self.pipe)
        if any(m[3] for m in moves.values()):
            self.gate.force = True
        if any(abs(dx) >= 1.0 or abs(dy) >= 1.0 for dx, dy in deltas.values()):
            self.motion_epoch += 1
        if membership:
            self._emit("scene", {"animated": False})
        elif deltas:
            self._emit("motion", deltas)

    # ---------------------------------------------------------------- translation
    def submit_all(self):
        """Queue every visible sentence, top of the screen first; in hover mode they
        are prefetched at low priority so hovering is instant."""
        base = 0 if self.s.mode == "all" else 100000
        wanted = []
        for k in self.scene.fresh():
            info = self.scene.labels[k]
            meta = info["meta"]
            wanted.extend(meta.get("sentences", []))
            for s, c in zip(meta.get("sentences", []), meta.get("contexts", [])):
                self.queue.submit(s, c, base + info["box"][1], k)
        self.queue.set_wanted(wanted)

    def boost(self, k):
        info = self.scene.labels.get(k)
        if info:
            for s, c in zip(info["meta"].get("sentences", []), info["meta"].get("contexts", [])):
                self.queue.submit(s, c, -1, k)

    def _translated_from_worker(self, k, sentence):        # worker thread
        self.post(self._ui_translated, sentence)

    def _ui_translated(self, sentence):
        self._emit("translated", sentence)
        self._emit("status", self.status())
