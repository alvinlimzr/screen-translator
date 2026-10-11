import time
import unittest

import numpy as np

from motion import offscreen, phase_shift, stale_ids, track

RNG = np.random.default_rng(7)


def page(h=1400, w=900, seed=1):
    """Synthetic web page / comic: light background with dark 'text lines'."""
    rng = np.random.default_rng(seed)
    img = np.full((h, w), 235, np.float32)
    y = 10
    while y < h - 20:
        lh = int(rng.integers(10, 18))
        x = int(rng.integers(10, 60))
        while x < w - 40:
            ww = int(rng.integers(6, 40))
            img[y:y + lh, x:x + ww] = rng.integers(20, 90)
            x += ww + int(rng.integers(3, 12))
        y += lh + int(rng.integers(8, 30))
    return img


def screen_view(content, scroll, h=585, w=900, sidebar=0):
    """What the screen shows when `content` is scrolled by `scroll` px, with an
    optional static sidebar on the left (doesn't scroll)."""
    view = content[scroll:scroll + h, :w].copy()
    if sidebar:
        view[:, :sidebar] = 60
        view[::25, :sidebar] = 200
    view += RNG.normal(0, 1.5, view.shape).astype(np.float32)        # capture noise
    return view


class PhaseShiftTests(unittest.TestCase):
    def test_recovers_known_shift(self):
        a = page(300, 300)[50:250, 50:250]
        b = page(300, 300)[50 + 13:250 + 13, 50 - 7:250 - 7]            # content moved up 13, right 7
        dy, dx, peak = phase_shift(a, b)
        self.assertAlmostEqual(dy, -13, delta=0.6)
        self.assertAlmostEqual(dx, 7, delta=0.6)
        self.assertGreater(peak, 0.3)

    def test_unrelated_patches_have_weak_peak(self):
        _, _, peak = phase_shift(page(200, 200, seed=1), page(200, 200, seed=99))
        self.assertLess(peak, 0.12)


class TrackTests(unittest.TestCase):
    def setUp(self):
        self.content = page()
        self.boxes = {1: (100, 100, 500, 140), 2: (200, 300, 700, 380), 3: (150, 400, 600, 520),
                      4: (60, 150, 840, 420)}                              # a big paragraph

    def test_scroll_is_followed_per_chunk(self):
        prev = screen_view(self.content, 200)
        for step in (5, 22, 37, 50):
            cur = screen_view(self.content, 200 + step)
            res = track(prev, cur, self.boxes)
            for k in self.boxes:
                dx, dy, conf, changed = res[k]
                self.assertAlmostEqual(dy, -step, delta=1.0, msg=(k, step, res[k]))
                self.assertAlmostEqual(dx, 0, delta=1.0)
                self.assertFalse(changed)

    def test_static_sidebar_does_not_fool_it(self):
        prev = screen_view(self.content, 200, sidebar=90)
        cur = screen_view(self.content, 230, sidebar=90)
        res = track(prev, cur, {1: (100, 100, 500, 140)})
        self.assertAlmostEqual(res[1][1], -30, delta=1.0)

    def test_still_screen_reports_no_motion(self):
        prev = screen_view(self.content, 200)
        res = track(prev, prev.copy(), self.boxes)
        self.assertTrue(all(abs(v[0]) < 0.5 and abs(v[1]) < 0.5 and not v[3] for v in res.values()))

    def test_page_switch_is_reported_as_changed(self):
        prev = screen_view(self.content, 200)
        cur = screen_view(page(seed=42), 200)
        res = track(prev, cur, self.boxes)
        self.assertTrue(all(v[3] for v in res.values()), res)

    def test_speed(self):
        prev, cur = screen_view(self.content, 200), screen_view(self.content, 220)
        boxes = {i: (50 + (i % 3) * 280, 20 + (i // 3) * 40, 300 + (i % 3) * 280, 50 + (i // 3) * 40)
                 for i in range(40)}
        t0 = time.perf_counter()
        track(prev, cur, boxes)
        dt = time.perf_counter() - t0
        print(f"\n  40 chunks tracked in {dt * 1000:.1f} ms on this machine")
        self.assertLess(dt, 0.5)


def thumb(view, tw=160, th=100):
    """Downscale like the app's change-detection thumbnail (block average)."""
    h, w = view.shape
    v = view[: h - h % th, : w - w % tw]
    small = v.reshape(th, v.shape[0] // th, tw, v.shape[1] // tw).mean(axis=(1, 3))
    return bytes(np.clip(small, 0, 255).astype(np.uint8).ravel())


class StaleAfterScrollTests(unittest.TestCase):
    """The v1.3 bug: translations stayed after scrolling. Now any chunk whose area
    changed since the last OCR scan is hidden at the next 0.4 s check."""

    def setUp(self):
        self.content = page(h=2400, w=1000, seed=3)
        # chunks found by OCR at scroll 0 (screen fractions); one in the static sidebar
        self.boxes = {"a": (0.15, 0.10, 0.60, 0.16), "b": (0.20, 0.40, 0.90, 0.52),
                      "c": (0.15, 0.80, 0.55, 0.86), "side": (0.01, 0.30, 0.08, 0.36)}

    def view(self, scroll):
        return screen_view(self.content, scroll, h=600, w=1000, sidebar=90)

    def test_no_change_no_stale(self):
        ref = thumb(self.view(0))
        self.assertEqual(stale_ids(thumb(self.view(0)), ref, 160, 100, self.boxes), set())

    def test_scrolled_page_hides_page_chunks_not_sidebar(self):
        ref = thumb(self.view(0))
        for scroll in (40, 150, 600):
            got = stale_ids(thumb(self.view(scroll)), ref, 160, 100, self.boxes)
            self.assertEqual(got, {"a", "b", "c"}, scroll)


class HelperTests(unittest.TestCase):
    def test_offscreen(self):
        self.assertTrue(offscreen((0, -100, 200, -10), (1000, 800)))
        self.assertTrue(offscreen((0, -90, 200, 10), (1000, 800)))       # 10% visible
        self.assertFalse(offscreen((0, -10, 200, 90), (1000, 800)))

    def test_stale_ids(self):
        tw, th = 160, 100
        ref = bytes([200] * tw * th)
        now = bytearray(ref)
        for y in range(10, 30):                                           # top band changed
            for x in range(tw):
                now[y * tw + x] = 30
        boxes = {"top": (0.1, 0.12, 0.6, 0.25), "bottom": (0.1, 0.6, 0.6, 0.7)}
        self.assertEqual(stale_ids(bytes(now), ref, tw, th, boxes), {"top"})
        self.assertEqual(stale_ids(ref, None, tw, th, boxes), set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
