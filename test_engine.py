"""Engine + scene tests with a fake screen (a tall page you can scroll) and a fake
OCR that 'reads' the text blocks visible at the current scroll position."""
import time
import unittest

import numpy as np

from engine import Engine, thumbnail, to_gray
from settings import Settings

W, H = 900, 600                       # screen, in points (= pixels here)
BLOCKS = [                            # (text, (l, t, r, b)) in PAGE coordinates
    ("ข่าวเศรษฐกิจวันนี้", (100, 80, 500, 120)),
    ("รัฐบาลประกาศมาตรการใหม่เพื่อกระตุ้นเศรษฐกิจ", (100, 200, 800, 240)),
    ("ร้านนี้อาหารอร่อยมาก แต่ราคาค่อนข้างแพง", (100, 420, 760, 460)),
    ("พรุ่งนี้ฝนจะตกหนักในกรุงเทพ", (100, 900, 600, 940)),
    ("ขอบคุณที่อ่านจนจบ", (100, 1500, 500, 1540)),
]


def make_page():
    rng = np.random.default_rng(5)
    page = np.full((2200, W, 3), 240, np.uint8)
    for _t, (l, t, r, b) in BLOCKS:
        for x in range(l, r, 14):                         # glyph-like strokes
            page[t + 6:b - 6, x:x + int(rng.integers(5, 11))] = int(rng.integers(10, 80))
    page[:, :60] = 50                                     # static sidebar
    return page


class FakeCapture:
    excluded = True

    def __init__(self):
        self.page, self.scroll = make_page(), 0

    def grab(self, px_per_pt):
        view = self.page[self.scroll:self.scroll + H].copy()
        if px_per_pt and px_per_pt != 1.0:
            step = int(round(1 / px_per_pt))
            return view[::step, ::step], 1.0 / step
        return view, 1.0


class FakeOCR:
    def __init__(self, cap, settings):
        self.cap, self.s, self.calls = cap, settings, 0

    def read(self, img, lang):
        self.calls += 1
        ox, oy = (self.s.roi[0], self.s.roi[1]) if self.s.roi else (0, 0)
        out = []
        for text, (l, t, r, b) in BLOCKS:
            t2, b2 = t - self.cap.scroll, b - self.cap.scroll
            if t2 >= 0 and b2 <= H:
                if self.s.roi and not (self.s.roi[0] <= l and r <= self.s.roi[2]
                                       and self.s.roi[1] <= t2 and b2 <= self.s.roi[3]):
                    continue
                out.append((text, (l - ox, t2 - oy, r - ox, b2 - oy)))
        return out


class FakeTranslator:
    key, loaded = "fake", True

    def translate(self, text, context=None, src="th", tgt="en"):
        return f"EN:{text}"


def make_engine(**kw):
    s = Settings(scan_interval=0.0, **kw)
    cap = FakeCapture()
    ocr = FakeOCR(cap, s)
    later = []
    e = Engine(s, FakeTranslator(), None, cap, ocr, (W, H),
               post=lambda fn, *a: fn(*a), post_later=lambda sec, fn: later.append(fn))
    events = []
    e.listeners.append(lambda kind, p: events.append(kind))
    return e, cap, ocr, later, events


def visible_texts(e):
    return sorted(e.scene.labels[k]["meta"]["sentences"][0] for k in e.scene.fresh())


def tick(e, n=1):
    out = None
    for _ in range(n):
        e.gate.last_scan_time = -1e9          # tests don't wait for the interval
        out = e.check_once()
    return out


class EngineTests(unittest.TestCase):
    def test_first_scan_then_idle(self):
        e, cap, ocr, *_ = make_engine()
        self.assertEqual(tick(e), "scanned")
        self.assertEqual(len(visible_texts(e)), 3)
        self.assertEqual([tick(e) for _ in range(5)], ["skipped"] * 5)   # idle screen: no OCR
        self.assertEqual(ocr.calls, 1)
        e.stop()

    def test_old_translations_hide_while_scrolling_and_return_when_settled(self):
        """The v1.3 bug: translations stayed on screen after scrolling away."""
        e, cap, ocr, later, events = make_engine()
        tick(e)
        before = {k: e.scene.labels[k]["box"] for k in e.scene.fresh()}
        cap.scroll = 120                       # user scrolls...
        self.assertEqual(tick(e), "skipped")   # ...screen still moving: no OCR yet
        self.assertEqual(visible_texts(e), [], "stale cards must hide immediately")
        cap.scroll = 160
        tick(e)                                # still moving
        self.assertEqual(tick(e), "scanned")   # stopped -> scan
        self.assertEqual(len(visible_texts(e)), 2)   # the headline scrolled off the top
        moved = [k for k in e.scene.fresh() if k in before]
        self.assertTrue(moved)
        for k in moved:                        # same chunk ids, new positions
            self.assertAlmostEqual(e.scene.labels[k]["box"][1], before[k][1] - 160)
        e.stop()

    def test_scrolled_far_away_chunks_disappear(self):
        e, cap, ocr, later, _ = make_engine()
        tick(e)
        cap.scroll = 1300                      # only the last block is on screen now
        tick(e)
        tick(e)                                # settled -> scan
        self.assertEqual(visible_texts(e), ["ขอบคุณที่อ่านจนจบ"])
        self.assertTrue(later, "a quick re-check is scheduled for missing chunks")
        later.pop()()
        tick(e)
        self.assertEqual(len(e.scene.labels), 1)   # old ones removed for good
        e.stop()

    def test_area_selection_limits_and_offsets(self):
        e, cap, ocr, *_ = make_engine(roi=[80, 180, 820, 480])
        tick(e)
        texts = visible_texts(e)
        self.assertEqual(len(texts), 2)
        box = next(e.scene.labels[k]["box"] for k in e.scene.fresh()
                   if e.scene.labels[k]["meta"]["sentences"][0].startswith("รัฐบาล"))
        self.assertEqual(box, (100, 200, 800, 240))   # mapped back to screen coordinates
        e.stop()

    def test_live_tracking_glides_and_drops(self):
        e, cap, ocr, later, events = make_engine(tracking="live")
        tick(e)
        k = next(k for k in e.scene.fresh()
                 if e.scene.labels[k]["meta"]["sentences"][0].startswith("ข่าว"))
        prev = e.live_once(None)
        cap.scroll = 30
        prev = e.live_once(prev)               # content moved up 30 pt
        self.assertAlmostEqual(e.scene.labels[k]["box"][1], 80 - 30, delta=1.5)
        self.assertIn("motion", events)
        for s in (60, 90, 120):                # keep scrolling: headline leaves the screen
            cap.scroll = s
            prev = e.live_once(prev)
        self.assertNotIn(k, e.scene.labels)
        e.stop()

    def test_translations_arrive(self):
        e, *_ = make_engine()
        tick(e)
        end = time.time() + 2
        while len(e.queue.cache) < 3 and time.time() < end:
            time.sleep(0.02)
        k = e.scene.fresh()[0]
        pairs, owners = e.scene.pairs_for((k,), e.queue.cache)
        self.assertTrue(pairs[0][1].startswith("EN:"))
        e.stop()


class HelperTests(unittest.TestCase):
    def test_thumbnail_and_gray(self):
        img = np.zeros((600, 900, 3), np.uint8)
        img[:, :450] = 255
        th = thumbnail(to_gray(img))
        self.assertEqual(len(th), 160 * 100)
        self.assertEqual(th[0], 255)
        self.assertEqual(th[159], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
