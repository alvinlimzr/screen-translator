import os
import tempfile
import time
import unittest

from PIL import Image, ImageDraw

from chunker import (Line, group_paragraphs, join_lines, sample_background,
                     split_sentences, text_color_for)
from favorites import Favorites
from pipeline import Pipeline, TranslateQueue, sentence_context


def L(text, x, y, w=600, h=30):
    return Line(text, (x, y, x + w, y + h))


class GroupTests(unittest.TestCase):
    def test_paragraphs_and_headline_split(self):
        lines = [
            L("ข่าวด่วนวันนี้", 100, 50, 400, 50),                 # big headline
            L("รัฐบาลประกาศมาตรการใหม่", 100, 130),                # paragraph 1
            L("เพื่อกระตุ้นเศรษฐกิจในปีหน้า", 100, 165, 500),
            L("ร้านนี้อาหารอร่อยมาก", 100, 260),                   # paragraph 2 (big gap)
            L("แต่ราคาค่อนข้างแพง", 100, 295, 300),
            L("เข้าสู่ระบบ", 900, 130, 120),                       # button, other column
        ]
        paras = group_paragraphs(lines)
        texts = sorted(p.text for p in paras)
        self.assertEqual(texts, sorted([
            "ข่าวด่วนวันนี้",
            "รัฐบาลประกาศมาตรการใหม่เพื่อกระตุ้นเศรษฐกิจในปีหน้า",
            "ร้านนี้อาหารอร่อยมากแต่ราคาค่อนข้างแพง",
            "เข้าสู่ระบบ",
        ]))
        p1 = next(p for p in paras if p.text.startswith("รัฐบาล"))
        self.assertEqual(p1.box, (100, 130, 700, 195))

    def test_headline_close_to_body_stays_separate(self):
        lines = [L("ข่าวเศรษฐกิจ", 200, 100, 500, 80),
                 L("รัฐบาลประกาศมาตรการใหม่", 200, 240, 2000, 60),
                 L("เพื่อกระตุ้นเศรษฐกิจ", 200, 310, 2000, 60)]
        self.assertEqual(sorted(p.text for p in group_paragraphs(lines)),
                         ["ข่าวเศรษฐกิจ", "รัฐบาลประกาศมาตรการใหม่เพื่อกระตุ้นเศรษฐกิจ"])

    def test_thai_line_heights_vary_a_bit(self):
        lines = [L("ที่นี่มีอาหารอร่อย", 0, 0, 500, 30), L("ปู่ย่าตายายชอบมาก", 0, 40, 500, 36)]
        self.assertEqual(len(group_paragraphs(lines)), 1)

    def test_two_columns_same_rows_stay_apart(self):
        lines = [L("ซ้ายบรรทัดแรก", 0, 0, 300), L("ขวาบรรทัดแรก", 400, 0, 300),
                 L("ซ้ายบรรทัดสอง", 0, 35, 300), L("ขวาบรรทัดสอง", 400, 35, 300)]
        texts = sorted(p.text for p in group_paragraphs(lines))
        self.assertEqual(texts, ["ขวาบรรทัดแรกขวาบรรทัดสอง", "ซ้ายบรรทัดแรกซ้ายบรรทัดสอง"])

    def test_join_lines(self):
        self.assertEqual(join_lines(["สวัสดี", "ครับ"]), "สวัสดีครับ")
        self.assertEqual(join_lines(["Hello", "world"]), "Hello world")
        self.assertEqual(join_lines(["ราคา 100", "บาท"]), "ราคา 100 บาท")


class SplitTests(unittest.TestCase):
    def test_thai_short_paragraph_stays_whole(self):
        text = ("รัฐบาลประกาศมาตรการใหม่เพื่อกระตุ้นเศรษฐกิจในปีหน้า "
                "นายกรัฐมนตรีกล่าวว่าจะเริ่มใช้ตั้งแต่เดือนมกราคม")
        self.assertEqual(split_sentences(text, "th"), [text])
        bubble = "แหะ แหะ พวกนั้นพวกห้อง B นี่นาถึงจะไม่ได้ตังใจไปได้ยิน"
        self.assertEqual(split_sentences(bubble, "th"), [bubble])     # no cut after "B"

    def test_thai_long_paragraph_split_at_spaces(self):
        a = "รัฐบาลประกาศมาตรการใหม่เพื่อกระตุ้นเศรษฐกิจในปีหน้าโดยเน้นการท่องเที่ยวและการส่งออก"
        b = "นายกรัฐมนตรีกล่าวว่าจะเริ่มใช้ตั้งแต่เดือนมกราคมและคาดว่าจะช่วยให้ประชาชนมีรายได้เพิ่มขึ้น"
        c = "นักวิเคราะห์หลายคนมองว่ามาตรการนี้อาจช่วยกระตุ้นการใช้จ่ายในระยะสั้นได้ดี"
        parts = split_sentences(" ".join([a, b, c]), "th")
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 160 for p in parts))
        self.assertEqual(" ".join(parts), " ".join([a, b, c]))

    def test_thai_short_pieces_merge(self):
        self.assertEqual(split_sentences("ราคา 100 บาท", "th"), ["ราคา 100 บาท"])
        self.assertEqual(split_sentences("สวัสดีครับ วันนี้อากาศดีมาก", "th"),
                         ["สวัสดีครับ วันนี้อากาศดีมาก"])

    def test_other_langs(self):
        self.assertEqual(split_sentences("今日は晴れです。明日は雨です。", "ja"),
                         ["今日は晴れです。明日は雨です。"])   # both short -> merged
        self.assertEqual(
            split_sentences("Bonjour à tous les amis. Aujourd'hui il fait très beau dehors.", "fr"),
            ["Bonjour à tous les amis.", "Aujourd'hui il fait très beau dehors."])


class ColourTests(unittest.TestCase):
    def test_background_and_text_colour(self):
        img = Image.new("RGB", (400, 200), (250, 245, 230))
        d = ImageDraw.Draw(img)
        d.rectangle([100, 80, 300, 120], fill=(10, 10, 10))       # "text" pixels
        bg = sample_background(img, (100, 80, 300, 120))
        self.assertEqual(bg, (250, 245, 230))
        self.assertEqual(text_color_for(bg), (20, 20, 20))
        self.assertEqual(text_color_for((30, 30, 40)), (245, 245, 245))


class FakeTranslator:
    def __init__(self, delay=0.0):
        self.calls, self.delay, self.started = [], delay, []

    def translate(self, text, context=None, src="th", tgt="en"):
        self.started.append(text)
        time.sleep(self.delay)
        self.calls.append((text, context))
        return "EN(" + text + ")"


PAGE = [("ข่าวเศรษฐกิจ", (200, 100, 700, 180)),
        ("1. รัฐบาลประกาศมาตรการใหม่", (200, 260, 1400, 320)),
        ("เพื่อกระตุ้นเศรษฐกิจในปีหน้า", (200, 330, 1200, 390)),
        ("Login", (1600, 260, 1700, 320))]


class PipelineTests(unittest.TestCase):
    def test_chunks_carry_sentences_and_context(self):
        p = Pipeline("th")
        labels = p.step(PAGE, (2, 2))
        self.assertEqual(len(labels), 2)
        body = next(l for l in labels if l.text.startswith("รัฐบาล"))
        self.assertEqual(body.box, (100, 130, 700, 195))           # points
        self.assertEqual(body.meta["sentences"],
                         ["รัฐบาลประกาศมาตรการใหม่เพื่อกระตุ้นเศรษฐกิจในปีหน้า"])
        self.assertEqual(body.meta["contexts"], ["ข่าวเศรษฐกิจ"])   # the headline
        moved = [(t, (b[0], b[1] - 100, b[2], b[3] - 100)) for t, b in PAGE]
        labels2 = p.step(moved, (2, 2))
        self.assertEqual({l.status for l in labels2}, {"moved"})

    def test_multi_sentence_context_includes_paragraph(self):
        s = ["ประโยคที่หนึ่งยาวพอสมควรเลยนะ", "ประโยคที่สองก็ยาวพอสมควรเหมือนกัน"]
        self.assertEqual(sentence_context(s, 1, ["หัวข้อ"]), " ".join(s) + "\nหัวข้อ")
        self.assertIsNone(sentence_context(["เดี่ยว"], 0, []))


class GlueAndTrackingTests(unittest.TestCase):
    def test_vertical_bubble_with_number_line_is_one_chunk(self):
        # the "แหม... คุณที่ " 1 " เองหรอกเหรอ~" bubble from the user's comic page
        col = ["แหม...", "คุณที่1", "ที่ไหนได้", "ก็เป็น", "คุณ", "ที่", '" 1 "', "เอง", "หรอก", "เหรอ~"]
        lines = [(t, (1540, 1470 + i * 110, 1860, 1560 + i * 110)) for i, t in enumerate(col)]
        labels = Pipeline("th").step(lines, (2, 2))
        self.assertEqual(len(labels), 1, [l.text for l in labels])
        self.assertIn("1", labels[0].text)
        self.assertTrue(labels[0].text.endswith("เหรอ~"))

    def test_number_only_chunk_is_dropped(self):
        labels = Pipeline("th").step([("8", (1500, 2800, 1530, 2840))], (2, 2))
        self.assertEqual(labels, [])

    def test_shift_and_drop_keep_tracker_in_sync(self):
        p = Pipeline("th")
        first = p.step(PAGE, (2, 2))
        body = next(l for l in first if l.text.startswith("รัฐบาล"))
        p.shift(body.id, 0, -50)                       # live tracking saw it scroll up 50 pt
        moved = [(t, (b[0], b[1] - 100, b[2], b[3] - 100)) for t, b in PAGE]
        again = {l.id: l for l in p.step(moved, (2, 2))}
        self.assertEqual(again[body.id].status, "same")   # already where OCR found it
        p.drop(body.id)
        self.assertNotIn(body.id, [l.id for l in p.tracker.labels])


class QueueTests(unittest.TestCase):
    def _wait(self, cond, timeout=3.0):
        end = time.time() + timeout
        while time.time() < end and not cond():
            time.sleep(0.01)
        self.assertTrue(cond())

    def test_priority_boost_skip_and_cache(self):
        tr = FakeTranslator(delay=0.05)
        done = []
        q = TranslateQueue(tr, lambda lid, s: done.append(s))
        q.submit("blocker", None, 0, 0)              # occupies the worker first
        self._wait(lambda: tr.started)
        for i in range(5):
            q.submit(f"s{i}", None, 10 + i, i)
        for i in range(5):
            q.submit(f"s{i}", None, 50, i)           # next scan re-submits: deduped
        self.assertEqual(len(q._heap), 5)
        q.submit("s4", "ctx", -1, 4)                 # hovered: jump the queue
        q.set_wanted(["blocker", "s0", "s1", "s4"])  # s2, s3 scrolled away
        self._wait(lambda: len(done) == 4)
        time.sleep(0.15)
        self.assertEqual(done, ["blocker", "s4", "s0", "s1"])
        self.assertEqual(tr.calls[1], ("s4", "ctx"))
        self.assertEqual(q.pending(), 0)
        q.submit("s0", None, 0, 0)                   # cached: not translated again
        time.sleep(0.15)
        self.assertEqual(len(tr.calls), 4)
        q.stop()


class FavoritesTests(unittest.TestCase):
    def test_toggle_and_persist(self):
        path = os.path.join(tempfile.mkdtemp(), "fav.json")
        f = Favorites(path)
        self.assertTrue(f.toggle("ซื้อเลย", "Buy now", "th", app="Safari"))
        self.assertTrue(Favorites(path).has("ซื้อเลย"))
        self.assertFalse(f.toggle("ซื้อเลย", "Buy now", "th"))
        self.assertFalse(Favorites(path).has("ซื้อเลย"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
