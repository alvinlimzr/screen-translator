import unittest

from tracker import (Block, Tracker, TranslationCache, blocks_from_ocrmac,
                     ease_box, normalize)


def B(text, x, y, w=100, h=30):
    return Block(text, (x, y, x + w, y + h))


def shift(blocks, dy):
    return [Block(b.text, (b.box[0], b.box[1] + dy, b.box[2], b.box[3] + dy))
            for b in blocks]


def PAGE():
    return [B("いつでも", 100, 100), B("Any time is okay.", 100, 200),
            B("私はいつでもいいです。", 100, 300), B("Share", 100, 400)]


def by_text(labels):
    return {l.text: l for l in labels}


class TrackerTests(unittest.TestCase):
    def test_first_cycle_all_new(self):
        out = Tracker().update(PAGE())
        self.assertEqual([l.status for l in out], ["new"] * 4)
        self.assertEqual([l.id for l in out], [1, 2, 3, 4])

    def test_unchanged_page_is_same(self):
        t = Tracker()
        t.update(PAGE())
        out = t.update(PAGE())
        self.assertTrue(all(l.status == "same" for l in out))

    def test_scroll_moves_labels_and_keeps_ids_and_translations(self):
        t = Tracker()
        first = t.update(PAGE())
        for l in first:
            l.translation = "T:" + l.text
        out = t.update(shift(PAGE(), -60))
        self.assertTrue(all(l.status == "moved" for l in out))
        self.assertEqual([l.id for l in out], [1, 2, 3, 4])
        self.assertEqual(out[0].prev_box, (100, 100, 200, 130))
        self.assertEqual(out[0].box, (100, 40, 200, 70))
        self.assertEqual(out[1].translation, "T:Any time is okay.")

    def test_tiny_shift_is_jitter(self):
        t = Tracker()
        t.update(PAGE())
        out = t.update(shift(PAGE(), 1))
        self.assertTrue(all(l.status == "same" for l in out))
        self.assertEqual(out[0].box, (100, 100, 200, 130))

    def test_disappears_after_grace(self):
        t = Tracker(grace=1)
        t.update(PAGE())
        out = t.update(PAGE()[1:])
        self.assertEqual(by_text(out)["いつでも"].status, "missing")
        out = t.update(PAGE()[1:])
        self.assertEqual(by_text(out)["いつでも"].status, "removed")
        self.assertNotIn("いつでも", [l.text for l in t.labels])

    def test_reappearing_after_one_miss_keeps_id(self):
        t = Tracker(grace=1)
        t.update(PAGE())
        t.update(PAGE()[1:])
        out = t.update(PAGE())
        l = by_text(out)["いつでも"]
        self.assertEqual((l.id, l.status), (1, "same"))

    def test_scroll_off_screen_and_new_content_arrives(self):
        t = Tracker(grace=0)
        t.update(PAGE())
        moved = [b for b in shift(PAGE(), -250) if b.box[1] >= 0]  # two stay visible
        moved.append(B("Newly revealed text", 100, 300))
        out = t.update(moved)
        s = {l.text: l.status for l in out}
        self.assertEqual(s["いつでも"], "removed")
        self.assertEqual(s["Any time is okay."], "removed")
        self.assertEqual(s["私はいつでもいいです。"], "moved")
        self.assertEqual(s["Share"], "moved")
        self.assertEqual(s["Newly revealed text"], "new")

    def test_duplicates_do_not_swap(self):
        t = Tracker()
        first = t.update([B("OK", 100, 100), B("OK", 100, 300), B("OK", 100, 500)])
        ids_by_y = {l.box[1]: l.id for l in first}
        out = t.update([B("OK", 100, 80), B("OK", 100, 280), B("OK", 100, 480)])
        for l in out:
            self.assertEqual(l.status, "moved")
            self.assertEqual(l.id, ids_by_y[l.prev_box[1]])

    def test_ocr_wobble_matches_fuzzily_and_retranslates(self):
        t = Tracker()
        first = t.update([B("Raishuu wa itsudemo ii desu.", 100, 100, 400)])
        first[0].translation = "As for next week, any time is fine."
        out = t.update([B("Raishuu wa itsudem0 ii desu.", 100, 100, 400)])
        self.assertEqual(out[0].id, 1)
        self.assertEqual(out[0].status, "same")
        self.assertIsNone(out[0].translation)

    def test_far_jump_is_a_new_label(self):
        t = Tracker(max_dist=500, grace=0)
        t.update([B("Hello there", 100, 100)])
        out = t.update([B("Hello there", 100, 2000)])
        s = sorted(l.status for l in out)
        self.assertEqual(s, ["new", "removed"])


class HelperTests(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize("Ｈｅｌｌｏ、 World!"), "helloworld")
        self.assertEqual(normalize("私は いつでも。"), "私はいつでも")
        self.assertEqual(normalize("สวัสดี"), "สวัสดี")  # Thai marks kept

    def test_ease_box(self):
        a, b = (0, 0, 10, 10), (100, 200, 110, 210)
        self.assertEqual(ease_box(a, b, 0), a)
        self.assertEqual(ease_box(a, b, 1), b)
        self.assertEqual(ease_box(a, b, 0.5), (50, 100, 60, 110))
        self.assertEqual(ease_box(a, b, 5), b)

    def test_blocks_from_ocrmac(self):
        b = blocks_from_ocrmac([("Hi", 1.0, (0.1, 0.8, 0.2, 0.1))], 1000, 500)[0]
        for got, want in zip(b.box, (100, 50, 300, 100)):
            self.assertAlmostEqual(got, want)

    def test_translation_cache_translates_once(self):
        calls = []
        cache = TranslationCache(lambda s: calls.append(s) or "EN:" + s)
        self.assertEqual(cache.get("いつでも"), "EN:いつでも")
        cache.get("いつでも。")  # same after normalization
        self.assertEqual(len(calls), 1)
        t = Tracker()
        labels = t.update(PAGE())
        cache.fill(labels)
        cache.fill(t.update(shift(PAGE(), -60)))
        self.assertEqual(len(calls), 4)  # 3 new + the one already cached


if __name__ == "__main__":
    unittest.main(verbosity=2)
