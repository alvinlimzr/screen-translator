import os
import tempfile
import time
import unittest

from cache import DiskCache
from change import ChangeGate, diff_fraction
from favorites import Favorites
from pipeline import TranslateQueue
from settings import Settings


class SettingsTests(unittest.TestCase):
    def test_roundtrip_and_validation(self):
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        s = Settings(source="ja", target="th", opacity=0.7, mode="all")
        s.save(p)
        self.assertEqual(Settings.load(p), s)
        with open(p, "w") as f:
            f.write('{"source": "xx", "opacity": 9, "scan_interval": "fast", "unknown": 1}')
        s2 = Settings.load(p)
        self.assertEqual((s2.source, s2.opacity, s2.scan_interval), ("th", 0.95, 1.0))

    def test_missing_or_corrupt_file_gives_defaults(self):
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        self.assertEqual(Settings.load(p), Settings())
        with open(p, "w") as f:
            f.write("{not json")
        self.assertEqual(Settings.load(p), Settings())

    def test_roi_and_tracking_validation(self):
        self.assertEqual(Settings(roi=[10, 20, 400, 300]).validate().roi, [10.0, 20.0, 400.0, 300.0])
        self.assertIsNone(Settings(roi=[10, 20, 15, 300]).validate().roi)        # too thin
        self.assertIsNone(Settings(roi="junk").validate().roi)
        self.assertEqual(Settings(tracking="warp").validate().tracking, "standard")

    def test_defaults(self):
        s = Settings()
        self.assertEqual((s.mode, s.show_outlines, s.source, s.target), ("hover", True, "th", "en"))


class CacheTests(unittest.TestCase):
    def test_per_model_and_pair(self):
        c = DiskCache(os.path.join(tempfile.mkdtemp(), "c.sqlite"))
        c.put("hymt8", "th", "en", "ซื้อเลย", "Buy now!")
        c.put("hymt8", "th", "ja", "ซื้อเลย", "今すぐ購入")
        c.put("nllb", "th", "en", "ซื้อเลย", "Buy it.")
        self.assertEqual(c.load("hymt8", "th", "en"), {"ซื้อเลย": "Buy now!"})
        self.assertEqual(c.load("hymt8", "th", "ja"), {"ซื้อเลย": "今すぐ購入"})
        self.assertEqual(c.count(), 3)
        c.clear()
        self.assertEqual(c.count(), 0)


class SlowFake:
    key = "fake"

    def __init__(self):
        self.calls = []

    def translate(self, text, context=None, src="th", tgt="en"):
        self.calls.append((text, src, tgt))
        time.sleep(0.02)
        return f"{tgt}:{text}"


class QueueDiskTests(unittest.TestCase):
    def test_second_session_is_instant(self):
        disk = DiskCache(os.path.join(tempfile.mkdtemp(), "c.sqlite"))
        tr, done = SlowFake(), []
        q = TranslateQueue(tr, lambda lid, s: done.append(s), "th", "ja", disk)
        q.submit("สวัสดี", None, 0, 1)
        end = time.time() + 2
        while not done and time.time() < end:
            time.sleep(0.01)
        q.stop()
        self.assertEqual(tr.calls, [("สวัสดี", "th", "ja")])
        q2 = TranslateQueue(SlowFake(), lambda *a: None, "th", "ja", disk)   # "next launch"
        self.assertEqual(q2.cache, {"สวัสดี": "ja:สวัสดี"})
        q3 = TranslateQueue(SlowFake(), lambda *a: None, "th", "en", disk)   # other pair
        self.assertEqual(q3.cache, {})
        q2.stop(); q3.stop()


class OutputCleanupTests(unittest.TestCase):
    def test_extra_paragraphs_dropped_for_one_line_source(self):
        from translators import first_paragraph
        out = "Those guys from Class B...\nI really wonder what colours they wear.\nUgh..."
        self.assertEqual(first_paragraph("นี่นาถึงจะไม่ได้ตั้งใจ", out), "Those guys from Class B...")
        self.assertEqual(first_paragraph("บรรทัดหนึ่ง\nบรรทัดสอง", "One\nTwo"), "One\nTwo")


class WarmupTests(unittest.TestCase):
    def test_warmup_loads_on_worker(self):
        class Lazy(SlowFake):
            loaded = False

            def _load(self):
                import threading
                self.thread = threading.current_thread().name
                time.sleep(0.05)
                self.loaded = True
        tr, done = Lazy(), []
        q = TranslateQueue(tr, lambda lid, s: done.append(lid))
        q.warmup()
        end = time.time() + 2
        while not tr.loaded and time.time() < end:
            time.sleep(0.01)
        time.sleep(0.05)
        self.assertTrue(tr.loaded)
        self.assertNotEqual(tr.thread, "MainThread")
        self.assertEqual(done, [-1])
        q.stop()


class ChangeGateTests(unittest.TestCase):
    A = bytes([200] * 1000)
    B = bytes([200] * 900 + [20] * 100)          # 10% of the screen changed

    def test_diff(self):
        self.assertEqual(diff_fraction(self.A, self.A), 0)
        self.assertAlmostEqual(diff_fraction(self.A, self.B), 0.1)
        self.assertEqual(diff_fraction(self.A, b"x"), 1.0)

    def test_idle_screen_is_scanned_once(self):
        g = ChangeGate(min_interval=1.0)
        self.assertTrue(g.should_scan(self.A, 0.0))
        self.assertFalse(any(g.should_scan(self.A, t / 10) for t in range(4, 100, 4)))

    def test_waits_for_scrolling_to_stop(self):
        g = ChangeGate(min_interval=0.5, max_wait=1.5)
        g.should_scan(self.A, 0.0)
        frames = [bytes([200] * (900 - 50 * k) + [20] * (100 + 50 * k)) for k in range(4)]
        self.assertFalse(g.should_scan(frames[0], 1.0))   # moving
        self.assertFalse(g.should_scan(frames[1], 1.4))   # still moving
        self.assertTrue(g.should_scan(frames[1], 1.8))    # stopped -> scan now

    def test_scans_anyway_if_it_never_stops(self):
        g = ChangeGate(min_interval=0.5, max_wait=1.5)
        g.should_scan(self.A, 0.0)
        t, scanned = 1.0, False
        for k in range(1, 8):                               # e.g. a playing video
            frame = bytes([(37 * k + i) % 256 for i in range(1000)])
            if g.should_scan(frame, t):
                scanned = True
                break
            t += 0.4
        self.assertTrue(scanned)
        self.assertLessEqual(t, 1.0 + 1.6)

    def test_force(self):
        g = ChangeGate(min_interval=0.0)
        g.should_scan(self.A, 0.0)
        g.force = True
        self.assertTrue(g.should_scan(self.A, 0.4))


class FavouritesTests(unittest.TestCase):
    def test_delete(self):
        f = Favorites(os.path.join(tempfile.mkdtemp(), "f.json"))
        f.toggle("ก", "A", "th")
        f.toggle("ข", "B", "th")
        f.delete("ก")
        self.assertEqual([i["source"] for i in f.items], ["ข"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
