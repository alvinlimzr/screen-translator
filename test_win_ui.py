"""Smoke test for the Windows UI (runs headless with Qt's offscreen platform)."""
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SCREEN_TRANSLATOR_DATA", tempfile.mkdtemp())

import numpy as np                                      # noqa: E402
from PySide6.QtCore import QRect                        # noqa: E402
from PySide6.QtGui import QIcon                         # noqa: E402
from PySide6.QtWidgets import QApplication              # noqa: E402

from engine import Engine                               # noqa: E402
from favorites import Favorites                         # noqa: E402
from settings import Settings                           # noqa: E402
from win.overlay import Bridge, Overlay, build_html     # noqa: E402
from win.ui import ControlWindow, FavouritesWindow      # noqa: E402

APP = QApplication.instance() or QApplication([])


class Cap:
    excluded = True

    def grab(self, ppp=0):
        img = np.full((600, 900, 3), 240, np.uint8)
        img[100:140, 100:500] = 30
        return img, 1.0


class OCR:
    def read(self, img, lang):
        return [("สวัสดีครับ วันนี้อากาศดีมาก", (100, 100, 500, 140))]


class Tr:
    key, loaded = "fake", True

    def translate(self, text, context=None, src="th", tgt="en"):
        return "Hello, the weather is lovely today."


class Stub:
    def __init__(self):
        self.settings = Settings()
        self.favs = Favorites(os.path.join(tempfile.mkdtemp(), "f.json"))
        self.downloading = False

    def model_available(self, k):
        return True

    def __getattr__(self, n):
        return lambda *a, **k: None


class WinUiTests(unittest.TestCase):
    def test_control_and_favourites_build(self):
        st = Stub()
        w = ControlWindow(st, QIcon())
        w.update_status({"paused": False, "chunks": 2, "queued": 0, "ocr": 0.5, "scans": 3,
                         "skipped": 9, "cached": 10, "capture": "sck"})
        w.set_download(0.5, "1 GB of 2 GB")
        w.set_download("done", "")
        self.assertIn("Running", w.state.text())
        f = FavouritesWindow(st.favs, QIcon())
        st.favs.toggle("ซื้อเลย", "Buy now!", "th", target="en")
        f.reload()
        self.assertEqual(f.table.rowCount(), 1)

    def test_overlay_draws_cards_and_tooltips(self):
        s = Settings(mode="all", scan_interval=0)
        bridge = Bridge()
        e = Engine(s, Tr(), None, Cap(), OCR(), (900, 600), bridge.post, bridge.post_later)
        ov = Overlay(e, s, Favorites(os.path.join(tempfile.mkdtemp(), "f.json")), QRect(0, 0, 900, 600),
                     lambda st: None, lambda r: None, lambda: None)
        self.assertEqual(e.check_once(), "scanned")
        for _ in range(100):
            APP.processEvents()
            if e.queue.cache:
                break
            import time
            time.sleep(0.02)
        ov.refresh()
        self.assertEqual(len(ov.cards), 1)
        card = next(iter(ov.cards.values()))
        self.assertEqual(card.pairs[0][1], "Hello, the weather is lovely today.")
        s.mode = "hover"
        ov.settings_changed({"mode"})
        self.assertEqual(len(ov.cards), 0)
        self.assertEqual(len(ov.outlines), 1)
        ov.show_tip(ov.groups[0])
        self.assertEqual(len(ov.cards), 1)
        e.stop()

    def test_html_ranges_match_text(self):
        favs = Favorites(os.path.join(tempfile.mkdtemp(), "f.json"))
        favs.toggle("a", "Alpha", "th")
        html, ranges = build_html([("a", "Alpha"), ("b", None), ("c", "Gamma <b>")], 14, favs)
        self.assertEqual(ranges, [(0, 7), (8, 20), (21, 30)])
        self.assertIn("&lt;b&gt;", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
