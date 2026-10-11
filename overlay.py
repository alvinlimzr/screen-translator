"""
The on-screen overlay (macOS). Driven by app.py; settings come from settings.py.

Scan loop (background, every ~0.4 s):
  capture (our windows excluded) -> thumbnail -> changed & settled? ->
  Vision OCR (whole screen or selected area) -> chunks (+context) -> tracker
  -> translation queue. Between scans, chunks whose screen area changed are
  hidden at once, so old translations never linger after you scroll.
Live tracking (experimental, background, ~12 fps):
  small greyscale frames; each chunk's motion is measured (motion.py) and its
  translation glides along with the text; chunks scrolled off screen vanish.
Main thread draws:
  Full overlay: semi-transparent cards over every chunk (overlaps merged).
  Hover: thin outline around each chunk group; rest the mouse on it to see the
         translation just below. Option-click a sentence to save it.
  Hold Control: hide everything to peek at the original.
"""
from __future__ import annotations

import threading
import time
import traceback

import objc
from AppKit import (NSAnimationContext, NSAttributedString,
                    NSBackgroundColorAttributeName, NSBackingStoreBuffered, NSColor,
                    NSEvent, NSEventModifierFlagControl, NSEventModifierFlagOption,
                    NSFont, NSFontAttributeName, NSFontWeightMedium,
                    NSFontWeightRegular, NSFontWeightSemibold,
                    NSForegroundColorAttributeName, NSLayoutManager,
                    NSMutableAttributedString, NSPanel, NSScreen, NSShadow,
                    NSShadowAttributeName, NSStatusWindowLevel, NSTextContainer,
                    NSTextField, NSTextStorage, NSTextView,
                    NSUnderlineColorAttributeName, NSUnderlineStyleAttributeName,
                    NSUnderlineStyleSingle, NSView, NSWindowAbove, NSWindowBelow,
                    NSWindowCollectionBehaviorCanJoinAllSpaces,
                    NSWindowCollectionBehaviorFullScreenAuxiliary,
                    NSWindowCollectionBehaviorStationary,
                    NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
                    NSWorkspace)
from Foundation import NSMakeRect, NSMakeSize, NSObject, NSPointInRect, NSTimer
from PyObjCTools import AppHelper
from Quartz import CAMediaTimingFunction, kCAMediaTimingFunctionLinear

import capture
import motion
from change import ChangeGate
from langtools import VISION_LANGS
from layout import Item, group_overlapping, plan
from pipeline import Pipeline, TranslateQueue
from settings import OCR_SCALE
from tracker import blocks_from_ocrmac

# ---------------------------------------------------------------- theme
def rgba(r, g, b, a=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)

BLACK = (0.04, 0.05, 0.07)
BLUE = (0.49, 0.83, 0.99)          # light sky blue  #7DD3FC
WHITE = (1.0, 1.0, 1.0)

PAD = 4
TIP_PAD_X, TIP_PAD_Y = 10, 7
MIN_PT, MAX_PT, TIP_PT = 11, 26, 14
HOVER_SLOP = 8
CHECK_EVERY = 0.4        # seconds between cheap change checks
LIVE_FPS = 12
LIVE_PX = 0.5            # live tracking frames: pixels per point


# ---------------------------------------------------------------- views & text
class FlippedView(NSView):
    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    def mouseDown_(self, event):
        ctrl = getattr(self, "controller", None)
        if ctrl is not None:
            ctrl.mouse_down(event)

    def mouseDragged_(self, event):
        ctrl = getattr(self, "controller", None)
        if ctrl is not None:
            ctrl.mouse_dragged(event)

    def mouseUp_(self, event):
        ctrl = getattr(self, "controller", None)
        if ctrl is not None:
            ctrl.mouse_up(event)


class CardView(FlippedView):
    def hitTest_(self, point):          # the whole card takes the click, not its text view
        return self if NSPointInRect(point, self.frame()) else None


def text_shadow():
    sh = NSShadow.alloc().init()
    sh.setShadowColor_(rgba(0, 0, 0, 0.85))
    sh.setShadowBlurRadius_(2.0)
    sh.setShadowOffset_(NSMakeSize(0, -0.5))
    return sh


def build_text(pairs, pt, favs, hover_idx=None):
    """pairs: [(source sentence, translation or None)] -> (attributed string, ranges)."""
    base = {NSFontAttributeName: NSFont.systemFontOfSize_weight_(pt, NSFontWeightMedium),
            NSForegroundColorAttributeName: rgba(*WHITE), NSShadowAttributeName: text_shadow()}
    pending = dict(base)
    pending[NSFontAttributeName] = NSFont.systemFontOfSize_weight_(pt * 0.85, NSFontWeightRegular)
    pending[NSForegroundColorAttributeName] = rgba(*BLUE, 0.75)
    s = NSMutableAttributedString.alloc().init()

    def add(text, attrs):
        s.appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(text, attrs))

    ranges = []
    for i, (src, out) in enumerate(pairs):
        if i:
            add(" ", base)
        start = s.length()
        if out is None:
            add("translating…", pending)
        else:
            attrs = dict(base)
            if i == hover_idx:
                attrs[NSBackgroundColorAttributeName] = rgba(*BLUE, 0.30)
            if favs.has(src):
                attrs[NSUnderlineStyleAttributeName] = NSUnderlineStyleSingle
                attrs[NSUnderlineColorAttributeName] = rgba(*BLUE)
                star = dict(attrs)
                star[NSForegroundColorAttributeName] = rgba(*BLUE)
                add("★ ", star)
            add(out, attrs)
        ranges.append((start, s.length()))
    return s, ranges


class TextBox:
    """TextKit 1 text system: cards draw with one, measuring uses one with identical
    settings, so measured height == drawn height."""

    def __init__(self):
        self.storage = NSTextStorage.alloc().init()
        self.lm = NSLayoutManager.alloc().init()
        self.storage.addLayoutManager_(self.lm)
        self.tc = NSTextContainer.alloc().initWithSize_(NSMakeSize(100, 1e7))
        self.tc.setLineFragmentPadding_(0)
        self.tc.setWidthTracksTextView_(False)
        self.lm.addTextContainer_(self.tc)

    def set(self, attr, width):
        self.tc.setContainerSize_(NSMakeSize(width, 1e7))
        self.storage.setAttributedString_(attr)
        self.lm.ensureLayoutForTextContainer_(self.tc)
        return self.lm.usedRectForTextContainer_(self.tc).size.height

    def make_view(self, frame):
        tv = NSTextView.alloc().initWithFrame_textContainer_(frame, self.tc)
        tv.setEditable_(False)
        tv.setSelectable_(False)
        tv.setDrawsBackground_(False)
        tv.setTextContainerInset_(NSMakeSize(0, 0))
        tv.setVerticallyResizable_(False)
        tv.setHorizontallyResizable_(False)
        return tv


def animate(duration, fn, done=None, linear=False):
    def group(ctx):
        ctx.setDuration_(duration)
        if linear:
            ctx.setTimingFunction_(CAMediaTimingFunction.functionWithName_(kCAMediaTimingFunctionLinear))
        fn()
    NSAnimationContext.runAnimationGroup_completionHandler_(group, done)


def moved_far(a, b, tol=1.0):
    return any(abs(x - y) > tol for x, y in zip(a, b))


def union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def shifted(frame, dx, dy):
    x, y, w, h = frame
    return (x + dx, y + dy, w, h)


# ================================================================ overlay
class Overlay(NSObject):

    @objc.python_method
    def setup(self, settings, translator, favs, disk, on_status, on_area):
        self.s = settings
        self.translator, self.favs, self.disk = translator, favs, disk
        self.on_status, self.on_area = on_status, on_area
        self.paused = settings.launch_paused
        # chunk id -> {"box", "meta", "missing", "stale"}; only fresh chunks are drawn
        self.labels = {}
        self.cards = {}          # card key (tuple of chunk ids) -> drawn card / tooltip
        self.outlines = {}       # group key -> outline view (hover mode)
        self.groups = []         # hover mode: groups of overlapping chunks
        self.measurer = TextBox()
        self._mcache = {}
        self.relayout_pending = False
        self.busy = True
        self.capture_mode = "auto"
        self.gate = ChangeGate(min_interval=settings.scan_interval)
        self.ref_thumb = None    # full-screen thumbnail at the last OCR scan
        self.norm_boxes = {}     # chunk id -> box as screen fractions (read by scan thread)
        self.live_boxes = {}     # chunk id -> box in points (read by live thread)
        self.motion_epoch = 0
        self.discards = 0
        self.live_fps = 0.0
        self.over, self.over_since = None, 0.0
        self.hover = None        # (card key, pair index) while Option is held
        self.mouse_on = False
        self.peeking = False
        self.selecting = None    # area selection: {"start", "view", ...}
        self.stats = {"ocr": None, "scans": 0, "skipped": 0}
        self._make_pipeline()

        screen = NSScreen.screens()[0]
        f = screen.frame()
        self.fw, self.fh = f.size.width, f.size.height
        self.native_scale = screen.backingScaleFactor()

        p = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            f, NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered, False)
        p.setOpaque_(False)
        p.setBackgroundColor_(NSColor.clearColor())
        p.setHasShadow_(False)
        p.setIgnoresMouseEvents_(True)
        p.setLevel_(NSStatusWindowLevel)
        p.setHidesOnDeactivate_(False)
        p.setBecomesKeyOnlyIfNeeded_(True)
        p.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                 | NSWindowCollectionBehaviorStationary
                                 | NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.content = FlippedView.alloc().initWithFrame_(NSMakeRect(0, 0, self.fw, self.fh))
        self.content.controller = self
        p.setContentView_(self.content)
        p.orderFrontRegardless()
        self.window = p

        self.make_bubble()
        self.area_view = None
        self.show_area()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1 / 30, self, "poll:", None, True)
        self.live_thread = threading.Thread(target=self.live_loop, daemon=True)
        self.live_thread.start()
        self.self_test()

    @objc.python_method
    def _make_pipeline(self):
        self.pipe = Pipeline(self.s.source, None, max_chunks=40)
        self.queue = TranslateQueue(self.translator, self.translated_from_worker,
                                    self.s.source, self.s.target, self.disk)
        self.queue.warmup()

    # ------------------------------------------------------------ settings
    @objc.python_method
    def apply_settings(self, changed):
        """Called by the app with the names of settings that changed."""
        if changed & {"source", "target"}:
            self.reset_language()
        if "mode" in changed:
            self.switch_mode()
        if "show_outlines" in changed:
            self.refresh_view()
        if "opacity" in changed:
            for e in self.cards.values():
                self.style_card(e["view"])
        if "text_scale" in changed:
            self.refresh_view()
        if changed & {"scan_interval", "ocr_quality", "tracking"}:
            self.gate.min_interval = self.s.scan_interval
            self.gate.force = True
        if "roi" in changed:
            self.show_area()
            self.reset_language()

    @objc.python_method
    def set_translator(self, translator):
        self.translator = translator
        self.reset_language()

    @objc.python_method
    def reset_language(self):
        """New language pair, model or area: forget what's on screen and rescan."""
        self.queue.stop()
        self.set_hover(None)
        for key in list(self.cards):
            self.remove_card(key, fade=False)
        for key in list(self.outlines):
            self.remove_outline(key)
        self.labels.clear()
        self.sync_boxes()
        self._make_pipeline()
        self.ref_thumb = None
        self.gate.force = True
        self.report()

    @objc.python_method
    def set_paused(self, paused):
        self.paused = paused
        if paused:
            self.window.orderOut_(None)
        else:
            self.window.orderFrontRegardless()
            self.gate.force = True
        self.report()

    @objc.python_method
    def switch_mode(self):
        self.set_hover(None)
        for key in list(self.cards):
            self.remove_card(key, fade=False)
        for key in list(self.outlines):
            self.remove_outline(key)
        self.refresh_view()
        self.submit_all()

    # ------------------------------------------------------------ status
    @objc.python_method
    def report(self):
        tr = self.translator
        busy = self.queue.pending() > 0 or self.queue.working
        self.on_status({
            "paused": self.paused,
            "chunks": len(self.fresh()),
            "queued": self.queue.pending() + (1 if self.queue.working else 0),
            "ocr": self.stats["ocr"],
            "scans": self.stats["scans"],
            "skipped": self.stats["skipped"],
            "cached": len(self.queue.cache),
            "capture": self.capture_mode,
            "live_fps": self.live_fps if self.s.tracking == "live" else None,
            "loading_model": self.queue.loading or (busy and not getattr(tr, "loaded", True)),
        })

    # ------------------------------------------------------------ chunk state
    @objc.python_method
    def fresh(self):
        """Chunks confirmed by the last scan whose screen area hasn't changed since."""
        return [lid for lid, i in self.labels.items() if not i["missing"] and not i["stale"]]

    @objc.python_method
    def sync_boxes(self):
        """Publish chunk boxes to the background threads (plain dict swaps)."""
        self.live_boxes = {lid: i["box"] for lid, i in self.labels.items() if not i["missing"]}
        self.norm_boxes = {lid: (b[0] / self.fw, b[1] / self.fh, b[2] / self.fw, b[3] / self.fh)
                           for lid, b in self.live_boxes.items()}

    # ------------------------------------------------------------ capture
    @objc.python_method
    def px_per_pt(self):
        return OCR_SCALE.get(self.s.ocr_quality) or self.native_scale

    @objc.python_method
    def grab(self):
        if self.capture_mode == "sck":
            return capture.capture_sck(self.px_per_pt())
        try:
            return capture.capture_screencapture()
        finally:
            if not self.paused:
                AppHelper.callAfter(self.window.orderFrontRegardless)

    @objc.python_method
    def self_test(self):
        """Can ScreenCaptureKit leave our overlay out of screenshots? A magenta square
        is centred on screen; if a capture still shows it, fall back to hiding."""
        x, y = self.fw / 2 - 20, self.fh / 2 - 20
        marker = NSView.alloc().initWithFrame_(NSMakeRect(x, y, 40, 40))
        marker.setWantsLayer_(True)
        marker.layer().setBackgroundColor_(NSColor.magentaColor().CGColor())
        self.content.addSubview_(marker)

        def run():
            time.sleep(0.6)
            mode = "hide"
            try:
                cg = capture.capture_sck(1.0)
                w, h = capture.size(cg)
                r, g, b = capture.pixel(cg, int(w / 2), int(h / 2))
                if not (r > 180 and g < 100 and b > 180):
                    mode = "sck"
            except Exception as e:
                print("ScreenCaptureKit not usable:", e)
            AppHelper.callAfter(self.finish_self_test, marker, mode)
        threading.Thread(target=run, daemon=True).start()

    @objc.python_method
    def finish_self_test(self, marker, mode):
        marker.removeFromSuperview()
        if self.paused:
            self.window.orderOut_(None)
        self.capture_mode = mode
        print("capture:", "ScreenCaptureKit, overlay excluded (no flicker)" if mode == "sck"
              else "hide overlay during each screenshot (brief flicker; live tracking off)")
        self.busy = False
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            CHECK_EVERY, self, "tick:", None, True)
        self.report()

    # ------------------------------------------------------------ scan cycle
    def tick_(self, _timer):
        if self.busy or self.paused or self.selecting:
            return
        if self.capture_mode == "hide":
            # hiding flickers, so no cheap checks: scan on the interval only
            if time.time() - self.gate.last_scan_time < self.s.scan_interval and not self.gate.force:
                return
            self.busy = True
            self.window.orderOut_(None)
            self.performSelector_withObject_afterDelay_("startWork:", None, 0.1)
        else:
            self.busy = True
            self.startWork_(None)

    def startWork_(self, _):
        threading.Thread(target=self.work, daemon=True).start()

    @objc.python_method
    def work(self):
        try:
            epoch = self.motion_epoch
            cg = self.grab()
            W, H = capture.size(cg)
            sx, sy = W / self.fw, H / self.fh
            thumb = capture.thumbnail(cg)
            roi = self.s.roi
            if roi:
                l, t, r, b = roi
                ox, oy = l * sx, t * sy
                img = capture.crop(cg, ox, oy, (r - l) * sx, (b - t) * sy)
                gate_thumb = capture.thumbnail(img)
            else:
                ox = oy = 0
                img, gate_thumb = cg, thumb
            now = time.time()

            if self.capture_mode == "hide":
                self.gate.last_scan_time, self.gate.force = now, False
            elif not self.gate.should_scan(gate_thumb, now):
                # no scan: hide translations whose screen area changed since the last one
                if not (self.s.tracking == "live" and self.capture_mode == "sck"):
                    stale = motion.stale_ids(thumb, self.ref_thumb, capture.THUMB_W,
                                             capture.THUMB_H, dict(self.norm_boxes))
                    AppHelper.callAfter(self.set_stale, stale)
                self.stats["skipped"] += 1
                AppHelper.callAfter(self.done)
                return

            t0 = time.perf_counter()
            res = capture.recognize(img, VISION_LANGS[self.s.source])
            self.stats["ocr"] = time.perf_counter() - t0
            self.stats["scans"] += 1
            iw, ih = capture.size(img)
            lines = [(blk.text, (blk.box[0] + ox, blk.box[1] + oy, blk.box[2] + ox, blk.box[3] + oy))
                     for blk in blocks_from_ocrmac(res, iw, ih)]
            if self.motion_epoch != epoch and self.discards < 2:
                self.discards += 1                # text moved while we were reading it
                self.gate.force = True
                AppHelper.callAfter(self.done)
                return
            self.discards = 0
            pipe = self.pipe
            labels = pipe.step(lines, (sx, sy))
            snap = [(lab.id, lab.status, lab.box, lab.meta) for lab in labels]
            AppHelper.callAfter(self.apply, snap, pipe, thumb)
        except Exception:
            traceback.print_exc()
            AppHelper.callAfter(self.done)

    @objc.python_method
    def done(self):
        self.busy = False
        self.report()

    @objc.python_method
    def apply(self, snap, pipe, thumb):
        if pipe is not self.pipe:             # language / area changed during the scan
            self.busy = False
            return
        any_missing = False
        for lid, status, box, meta in snap:
            if status == "removed":
                self.labels.pop(lid, None)
                continue
            missing = status == "missing"
            any_missing |= missing
            old = self.labels.get(lid)
            self.labels[lid] = {"box": box if not missing or not old else old["box"],
                                "meta": meta, "missing": missing, "stale": False}
        self.ref_thumb = thumb
        self.sync_boxes()
        self.submit_all()
        self.refresh_view(animated=True)
        if any_missing:                       # confirm disappearances soon, not "whenever"
            self.performSelector_withObject_afterDelay_("forceScan:", None, 0.7)
        self.busy = False
        self.report()

    def forceScan_(self, _):
        self.gate.force = True

    @objc.python_method
    def set_stale(self, ids):
        changed = False
        for lid, info in self.labels.items():
            st = lid in ids
            if info["stale"] != st:
                info["stale"] = st
                changed = True
        if changed:
            self.refresh_view()

    @objc.python_method
    def submit_all(self):
        """Queue every visible sentence, top of the screen first. In hover mode they
        are prefetched at low priority so hovering is instant."""
        base = 0 if self.s.mode == "all" else 100000
        wanted = []
        for lid in self.fresh():
            info = self.labels[lid]
            meta = info["meta"]
            wanted.extend(meta.get("sentences", []))
            for s, c in zip(meta.get("sentences", []), meta.get("contexts", [])):
                self.queue.submit(s, c, base + info["box"][1], lid)
        self.queue.set_wanted(wanted)

    @objc.python_method
    def boost(self, lid):
        info = self.labels.get(lid)
        if info:
            for s, c in zip(info["meta"].get("sentences", []), info["meta"].get("contexts", [])):
                self.queue.submit(s, c, -1, lid)

    @objc.python_method
    def translated_from_worker(self, lid, sentence):       # worker thread
        AppHelper.callAfter(self.translated, sentence)

    @objc.python_method
    def translated(self, sentence):
        if sentence:
            if self.s.mode == "all":
                self.schedule_relayout()
            else:
                for key in list(self.cards):
                    if any(sentence in self.labels.get(lid, {}).get("meta", {}).get("sentences", [])
                           for lid in key):
                        self.layout_tip(key)
        self.report()

    # ------------------------------------------------------------ live tracking
    @objc.python_method
    def live_loop(self):
        """Background: ~12 fps motion tracking while tracking == 'live'."""
        prev = None
        while True:
            t0 = time.time()
            if (self.s.tracking != "live" or self.paused or self.capture_mode != "sck"
                    or self.selecting or not self.live_boxes):
                prev = None
                time.sleep(0.25)
                continue
            try:
                data, w, h = capture.gray(capture.capture_sck(LIVE_PX))
                frame = motion.frame_from_bytes(data, w, h)
                boxes = dict(self.live_boxes)
                if prev is not None and prev.shape == frame.shape:
                    sx, sy = w / self.fw, h / self.fh
                    res = motion.track(prev, frame, {k: (b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy)
                                                    for k, b in boxes.items()})
                    moves = {k: (dx / sx, dy / sy, conf, ch) for k, (dx, dy, conf, ch) in res.items()
                             if ch or abs(dx) > 0.3 or abs(dy) > 0.3}
                    if moves:
                        AppHelper.callAfter(self.apply_motion, moves)
                prev = frame
            except Exception:
                traceback.print_exc()
                time.sleep(1.0)
            dt = time.time() - t0
            self.live_fps = 0.8 * self.live_fps + 0.2 * (1 / max(dt, 1 / LIVE_FPS))
            time.sleep(max(0.0, 1 / LIVE_FPS - dt))

    @objc.python_method
    def apply_motion(self, moves):
        """Main thread: glide everything with the text; drop chunks scrolled away."""
        deltas, membership_changed = {}, False
        for lid, (dx, dy, conf, changed) in moves.items():
            info = self.labels.get(lid)
            if info is None or info["missing"]:
                continue
            if changed and not info["stale"]:
                info["stale"] = True                  # content under it was replaced
                membership_changed = True
                self.gate.force = True
                continue
            if abs(dx) < 0.3 and abs(dy) < 0.3:
                continue
            l, t, r, b = info["box"]
            info["box"] = (l + dx, t + dy, r + dx, b + dy)
            self.pipe.shift(lid, dx, dy)
            deltas[lid] = (dx, dy)
            if motion.offscreen(info["box"], (self.fw, self.fh)):
                self.labels.pop(lid)
                self.pipe.drop(lid)
                membership_changed = True
        if any(abs(dx) >= 1.0 or abs(dy) >= 1.0 for dx, dy in deltas.values()):
            self.motion_epoch += 1
        self.sync_boxes()
        if membership_changed:
            self.refresh_view()
            return
        dur = 1 / LIVE_FPS
        for key, e in self.cards.items():
            ds = [deltas[k] for k in key if k in deltas]
            if ds:
                dx = sum(d[0] for d in ds) / len(ds)
                dy = sum(d[1] for d in ds) / len(ds)
                e["frame"] = shifted(e["frame"], dx, dy)
                v, fr = e["view"], NSMakeRect(*e["frame"])
                animate(dur, lambda v=v, fr=fr: v.animator().setFrame_(fr), linear=True)
        for key, v in self.outlines.items():
            ds = [deltas[k] for k in key if k in deltas]
            if ds:
                f = v.frame()
                fr = NSMakeRect(f.origin.x + sum(d[0] for d in ds) / len(ds),
                                f.origin.y + sum(d[1] for d in ds) / len(ds), f.size.width, f.size.height)
                animate(dur, lambda v=v, fr=fr: v.animator().setFrame_(fr), linear=True)

    # ------------------------------------------------------------ drawing
    @objc.python_method
    def refresh_view(self, animated=False):
        if self.s.mode == "all":
            for key in list(self.outlines):
                self.remove_outline(key)
            self.relayout()
            return
        fresh = self.fresh()
        self.groups = group_overlapping({lid: self.labels[lid]["box"] for lid in fresh}, gap=4)
        keys = set(self.groups)
        for key in list(self.outlines):
            if key not in keys or not self.s.show_outlines:
                self.remove_outline(key)
        if self.s.show_outlines:
            for key in self.groups:
                self.place_outline(key, animated)
        for key in list(self.cards):
            if key in keys:
                self.layout_tip(key, animated=animated)
            else:
                self.remove_card(key)

    @objc.python_method
    def group_box(self, key):
        return union([self.labels[k]["box"] for k in key if k in self.labels])

    @objc.python_method
    def pairs_for(self, key):
        out, owners = [], []
        for lid in key:
            if lid not in self.labels:
                continue
            for i, s in enumerate(self.labels[lid]["meta"].get("sentences", [])):
                out.append((s, self.queue.cache.get(s)))
                owners.append((lid, i))
        return out, owners

    @objc.python_method
    def measure(self, pairs, pt, width):
        key = (tuple(pairs), pt, round(width))
        h = self._mcache.get(key)
        if h is None:
            attr, _r = build_text(pairs, pt, self.favs)
            h = self.measurer.set(attr, width)
            self._mcache[key] = h
        return h

    # outlines (hover mode)
    @objc.python_method
    def place_outline(self, key, animated):
        l, t, r, b = self.group_box(key)
        frame = NSMakeRect(l - 3, t - 2, r - l + 6, b - t + 4)
        v = self.outlines.get(key)
        if v is None:
            v = NSView.alloc().initWithFrame_(frame)
            v.setWantsLayer_(True)
            v.layer().setCornerRadius_(4)
            self.content.addSubview_positioned_relativeTo_(v, NSWindowBelow, None)
            self.outlines[key] = v
            v.setAlphaValue_(0.0)
            animate(0.15, lambda v=v: v.animator().setAlphaValue_(1.0))
        elif animated:
            animate(0.3, lambda v=v, fr=frame: v.animator().setFrame_(fr))
        else:
            v.setFrame_(frame)
        self.style_outline(key)

    @objc.python_method
    def style_outline(self, key):
        v = self.outlines.get(key)
        if v is None:
            return
        hot = self.over is not None and self.over in key
        v.layer().setBorderColor_(rgba(*BLUE, 0.95 if hot else 0.55).CGColor())
        v.layer().setBorderWidth_(1.5 if hot else 1.0)
        v.layer().setBackgroundColor_(rgba(*BLUE, 0.06 if hot else 0.0).CGColor())

    @objc.python_method
    def remove_outline(self, key):
        v = self.outlines.pop(key, None)
        if v is not None:
            v.removeFromSuperview()

    # cards
    @objc.python_method
    def style_card(self, view):
        tip = self.s.mode == "hover"
        layer = view.layer()
        alpha = max(0.9, self.s.opacity) if tip else self.s.opacity
        layer.setBackgroundColor_(rgba(*BLACK, alpha).CGColor())
        layer.setCornerRadius_(6)
        layer.setBorderWidth_(1.0)
        layer.setBorderColor_(rgba(*BLUE, 0.65).CGColor())

    @objc.python_method
    def new_card(self, key, frame, fade):
        card = CardView.alloc().initWithFrame_(NSMakeRect(*frame))
        card.controller = self
        card.setWantsLayer_(True)
        self.style_card(card)
        tb = TextBox()
        tv = tb.make_view(NSMakeRect(0, 0, 10, 10))
        card.addSubview_(tv)
        self.content.addSubview_positioned_relativeTo_(card, NSWindowBelow, self.bubble)
        e = {"view": card, "tv": tv, "tb": tb, "frame": frame, "pairs": [], "owners": [],
             "ranges": [], "pt": TIP_PT}
        self.cards[key] = e
        if fade:
            card.setAlphaValue_(0.0)
            animate(0.2, lambda: card.animator().setAlphaValue_(1.0))
        return e

    @objc.python_method
    def remove_card(self, key, fade=True):
        e = self.cards.pop(key, None)
        if not e:
            return
        if self.hover and self.hover[0] == key:
            self.set_hover(None)
        v = e["view"]
        if fade:
            animate(0.2, lambda: v.animator().setAlphaValue_(0.0), lambda: v.removeFromSuperview())
        else:
            v.removeFromSuperview()

    @objc.python_method
    def fill(self, key, frame, text_frame, pt, pairs, owners, animated=False):
        e = self.cards[key]
        hover_idx = self.hover[1] if self.hover and self.hover[0] == key else None
        e.update(pairs=pairs, owners=owners, pt=pt)
        attr, e["ranges"] = build_text(pairs, pt, self.favs, hover_idx)
        tx, ty, tw, _ = text_frame
        th = e["tb"].set(attr, tw)
        x, y, w, h = frame
        h = max(h, th + 2 * ty)
        e["tv"].setFrame_(NSMakeRect(tx, ty, tw, th + 2))
        new = (x, y, w, h)
        v = e["view"]
        if animated and moved_far(e["frame"], new):
            animate(0.3, lambda: v.animator().setFrame_(NSMakeRect(*new)))
        else:
            v.setFrame_(NSMakeRect(*new))
        e["frame"] = new

    @objc.python_method
    def schedule_relayout(self):
        if not self.relayout_pending:
            self.relayout_pending = True
            self.performSelector_withObject_afterDelay_("relayoutNow:", None, 0.15)

    def relayoutNow_(self, _):
        self.relayout_pending = False
        if self.s.mode == "all":
            self.relayout()

    @objc.python_method
    def relayout(self):
        """Full overlay: plan cards for the fresh chunks (overlapping ones merged), then
        update / create / remove card views to match. Stale or missing chunks are never
        drawn or merged."""
        self._mcache = {}
        k = self.s.text_scale
        items = []
        for lid in self.fresh():
            info = self.labels[lid]
            if info["meta"].get("sentences"):
                pairs, _ = self.pairs_for((lid,))
                items.append(Item(lid, info["box"], info["meta"].get("line_h", 16) * k, pairs))
        planned = plan(items, self.measure, (self.fw, self.fh), pad=PAD,
                       min_pt=round(MIN_PT * k), max_pt=round(MAX_PT * k))
        keep = set()
        old_lids = {lid for key in self.cards for lid in key}
        for c in planned:
            key = tuple(sorted(c.keys))
            keep.add(key)
            existed = key in self.cards
            if not existed:
                self.new_card(key, c.frame, fade=not any(x in old_lids for x in key))
            self.fill(key, c.frame, c.text_frame, c.pt, c.pairs, c.owners, animated=existed)
        for key in list(self.cards):
            if key not in keep:
                self.remove_card(key)

    @objc.python_method
    def layout_tip(self, key, animated=False):
        """Hover mode: tooltip just below the group (above it if no room)."""
        if key not in self.cards or not any(k in self.labels for k in key):
            return
        self._mcache = {}
        pt = round(TIP_PT * self.s.text_scale)
        l, t, r, b = self.group_box(key)
        pairs, owners = self.pairs_for(key)
        attr, _ = build_text(pairs, pt, self.favs)
        inner = min(560, max(280, r - l), max(60, attr.size().width + 2))
        th = self.measure(pairs, pt, inner)
        w, h = inner + 2 * TIP_PAD_X, th + 2 * TIP_PAD_Y
        x = max(8, min(l, self.fw - w - 8))
        y = b + 6
        if y + h > self.fh - 8:
            y = max(8, t - h - 6)
        self.fill(key, (x, y, w, h), (TIP_PAD_X, TIP_PAD_Y, inner, th), pt, pairs, owners,
                  animated=animated)

    @objc.python_method
    def show_tip(self, key):
        if key in self.cards:
            return
        self.new_card(key, (0, 0, 10, 10), fade=True)
        self.layout_tip(key)

    @objc.python_method
    def rerender(self, key):
        e = self.cards.get(key)
        if not e:
            return
        if self.s.mode == "all":
            self.fill(key, e["frame"], (PAD, PAD, e["frame"][2] - 2 * PAD, 0), e["pt"],
                      e["pairs"], e["owners"])
        else:
            self.layout_tip(key)

    # ------------------------------------------------------------ bubble (original text)
    @objc.python_method
    def make_bubble(self):
        self.bubble = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        self.bubble.setWantsLayer_(True)
        self.bubble.layer().setBackgroundColor_(rgba(*BLACK, 0.95).CGColor())
        self.bubble.layer().setCornerRadius_(6)
        self.bubble.layer().setBorderWidth_(1.0)
        self.bubble.layer().setBorderColor_(rgba(*BLUE, 0.8).CGColor())
        self.bubble_tf = NSTextField.wrappingLabelWithString_("")
        self.bubble_tf.setTextColor_(rgba(*WHITE))
        self.bubble_tf.setFont_(NSFont.systemFontOfSize_(16))
        self.bubble.addSubview_(self.bubble_tf)
        self.bubble.setHidden_(True)
        self.content.addSubview_(self.bubble)

    @objc.python_method
    def show_bubble(self, text, card_frame):
        tf = self.bubble_tf
        tf.setStringValue_(text)
        tf.setPreferredMaxLayoutWidth_(min(560, self.fw - 40))
        s = tf.fittingSize()
        w, h = s.width + 20, s.height + 12
        tf.setFrame_(NSMakeRect(10, 6, s.width, s.height))
        cx, cy, cw, ch = card_frame
        x = max(8, min(cx, self.fw - w - 8))
        y = cy - h - 6
        if y < 0:
            y = cy + ch + 6
        self.bubble.setFrame_(NSMakeRect(x, y, w, h))
        self.bubble.setHidden_(False)

    # ------------------------------------------------------------ area (region of interest)
    @objc.python_method
    def show_area(self):
        """Faint outline + tag around the selected area (if any)."""
        if self.area_view is not None:
            self.area_view.removeFromSuperview()
            self.area_view = None
        roi = self.s.roi
        if not roi:
            return
        l, t, r, b = roi
        v = NSView.alloc().initWithFrame_(NSMakeRect(l, t, r - l, b - t))
        v.setWantsLayer_(True)
        v.layer().setBorderWidth_(1.0)
        v.layer().setBorderColor_(rgba(*BLUE, 0.35).CGColor())
        v.layer().setCornerRadius_(3)
        tag = NSTextField.labelWithString_(" translating this area ")
        tag.setFont_(NSFont.systemFontOfSize_weight_(10, NSFontWeightSemibold))
        tag.setTextColor_(rgba(*BLACK))
        tag.setDrawsBackground_(True)
        tag.setBackgroundColor_(rgba(*BLUE, 0.75))
        tag.sizeToFit()
        tag.setFrameOrigin_((0, 0))
        v.addSubview_(tag)
        self.content.addSubview_positioned_relativeTo_(v, NSWindowBelow, None)
        self.area_view = v

    @objc.python_method
    def start_select(self):
        """Let the user drag a rectangle; translation is then limited to it."""
        if self.selecting:
            return
        dim = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, self.fw, self.fh))
        dim.setWantsLayer_(True)
        dim.layer().setBackgroundColor_(rgba(0, 0, 0, 0.35).CGColor())
        hint = NSTextField.labelWithString_("  Drag to choose the area to translate  ·  click without dragging to cancel  ")
        hint.setFont_(NSFont.systemFontOfSize_weight_(15, NSFontWeightSemibold))
        hint.setTextColor_(rgba(*WHITE))
        hint.setDrawsBackground_(True)
        hint.setBackgroundColor_(rgba(*BLACK, 0.9))
        hint.sizeToFit()
        hint.setFrameOrigin_(((self.fw - hint.frame().size.width) / 2, 60))
        dim.addSubview_(hint)
        box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 0, 0))
        box.setWantsLayer_(True)
        box.layer().setBorderWidth_(2.0)
        box.layer().setBorderColor_(rgba(*BLUE).CGColor())
        box.layer().setBackgroundColor_(rgba(*BLUE, 0.10).CGColor())
        dim.addSubview_(box)
        self.content.addSubview_positioned_relativeTo_(dim, NSWindowAbove, None)
        self.selecting = {"dim": dim, "box": box, "start": None}
        self.window.setIgnoresMouseEvents_(False)
        self.mouse_on = True
        if self.paused:
            self.window.orderFrontRegardless()

    @objc.python_method
    def end_select(self, rect):
        sel, self.selecting = self.selecting, None
        sel["dim"].removeFromSuperview()
        self.window.setIgnoresMouseEvents_(True)
        self.mouse_on = False
        if self.paused:
            self.window.orderOut_(None)
        self.on_area(rect)

    @objc.python_method
    def event_point(self, event):
        return self.content.convertPoint_fromView_(event.locationInWindow(), None)

    @objc.python_method
    def mouse_down(self, event):
        if self.selecting:
            self.selecting["start"] = self.event_point(event)
            return
        self.clicked()

    @objc.python_method
    def mouse_dragged(self, event):
        sel = self.selecting
        if not sel or sel["start"] is None:
            return
        a, b = sel["start"], self.event_point(event)
        sel["box"].setFrame_(NSMakeRect(min(a.x, b.x), min(a.y, b.y), abs(a.x - b.x), abs(a.y - b.y)))

    @objc.python_method
    def mouse_up(self, event):
        sel = self.selecting
        if not sel:
            return
        if sel["start"] is None:
            self.end_select(None)
            return
        a, b = sel["start"], self.event_point(event)
        l, t, r, bt = min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y)
        self.end_select([l, t, r, bt] if r - l >= 20 and bt - t >= 20 else None)

    # ------------------------------------------------------------ mouse
    @objc.python_method
    def mouse_point(self):
        wp = self.window.convertPointFromScreen_(NSEvent.mouseLocation())
        return self.content.convertPoint_fromView_(wp, None)

    @objc.python_method
    def card_at(self, p):
        for key, e in self.cards.items():
            if NSPointInRect(p, e["view"].frame()):
                return key
        return None

    @objc.python_method
    def chunk_at(self, p):
        for lid in self.fresh():
            l, t, r, b = self.labels[lid]["box"]
            if l - HOVER_SLOP <= p.x <= r + HOVER_SLOP and t - HOVER_SLOP <= p.y <= b + HOVER_SLOP:
                return lid
        return None

    @objc.python_method
    def group_of(self, lid):
        return next((g for g in self.groups if lid in g), (lid,))

    @objc.python_method
    def pair_at(self, key, p):
        e = self.cards.get(key)
        if not e:
            return None
        tp = e["tv"].convertPoint_fromView_(p, self.content)
        res = e["tb"].lm.characterIndexForPoint_inTextContainer_fractionOfDistanceBetweenInsertionPoints_(
            tp, e["tb"].tc, None)
        idx = res[0] if isinstance(res, tuple) else res
        for i, (a, z) in enumerate(e["ranges"]):
            if a <= idx < z:
                return i
        return None

    def poll_(self, _timer):
        if self.paused or self.selecting:
            return
        try:
            flags = NSEvent.modifierFlags()
            peek = bool(flags & NSEventModifierFlagControl)        # hold ⌃ to see the original
            if peek != self.peeking:
                self.peeking = peek
                self.window.setAlphaValue_(0.0 if peek else 1.0)
            if peek:
                return
            now = time.time()
            p = self.mouse_point()
            key = self.card_at(p)
            if key is None:
                over = self.chunk_at(p)
            else:
                over = next((k for k in key if k in self.labels), None)
            if over != self.over:
                old, self.over, self.over_since = self.over, over, now
                if over is not None:
                    self.boost(over)
                for g in self.groups:
                    if (old is not None and old in g) or (over is not None and over in g):
                        self.style_outline(g)

            if self.s.mode == "hover":
                want = self.group_of(over) if over is not None else None
                if want is not None and want not in self.cards and now - self.over_since >= self.s.hover_delay:
                    self.show_tip(want)
                for k in list(self.cards):
                    if k != want and k != key:
                        self.remove_card(k)

            opt = bool(flags & NSEventModifierFlagOption)
            hit = None
            if opt and key is not None and key in self.cards:
                hit = (key, self.pair_at(key, p))
            want_mouse = hit is not None
            if want_mouse != self.mouse_on:
                self.window.setIgnoresMouseEvents_(not want_mouse)
                self.mouse_on = want_mouse
            if hit != self.hover:
                self.set_hover(hit)
        except Exception:
            traceback.print_exc()

    @objc.python_method
    def set_hover(self, hit):
        old, self.hover = self.hover, hit
        if old and old[0] in self.cards and (not hit or hit[0] != old[0]):
            self.rerender(old[0])
        if not hit or hit[1] is None or hit[0] not in self.cards:
            self.bubble.setHidden_(True)
            if hit and hit[0] in self.cards:
                self.rerender(hit[0])
            return
        key, i = hit
        self.rerender(key)
        if self.s.mode == "all":                   # in hover mode the original stays visible
            self.show_bubble(self.cards[key]["pairs"][i][0], self.cards[key]["frame"])
        else:
            self.bubble.setHidden_(True)

    @objc.python_method
    def clicked(self):
        if not self.hover or self.hover[1] is None or self.hover[0] not in self.cards:
            return
        key, i = self.hover
        e = self.cards[key]
        src, out = e["pairs"][i]
        if out is None:
            return
        lid = e["owners"][i][0]
        ctx = " ".join(self.labels[lid]["meta"].get("sentences", [])) if lid in self.labels else None
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        saved = self.favs.toggle(src, out, self.s.source, context=ctx,
                                 app=app.localizedName() if app else None, target=self.s.target)
        print(("★ saved: " if saved else "☆ removed: ") + f"{src}  ->  {out}")
        self.rerender(key)
        if self.s.mode == "all":
            self.schedule_relayout()
        self.on_status({"favourites_changed": True})
