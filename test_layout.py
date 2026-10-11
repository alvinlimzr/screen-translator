import math
import unittest

from chunker import Line, Paragraph, group_paragraphs, merge_touching
from layout import Item, _overlap, fit_card, group_overlapping, plan


def measure(pairs, pt, width):
    """Rough text model: each char ~0.55 em wide, lines 1.2 em tall."""
    chars = sum(len(en or "translating…") + 1 for _s, en in pairs)
    per_line = max(1, int(width / (pt * 0.55)))
    return math.ceil(chars / per_line) * pt * 1.2


def P(*boxes):
    return Paragraph([Line(f"ข้อความ{i}", b) for i, b in enumerate(boxes)])


SCREEN = (1440, 900)


class MergeTests(unittest.TestCase):
    def test_touching_handwritten_lines_merge(self):
        # uneven handwritten lines that the strict grouping left in 3 pieces
        paras = [P((115, 75, 410, 102), (115, 106, 405, 132)),
                 P((60, 139, 387, 167)),
                 P((235, 227, 395, 255))]
        merged = merge_touching(paras)
        self.assertEqual(len(merged), 2)
        self.assertEqual(len(merged[0].lines), 3)
        self.assertEqual([l.box[1] for l in merged[0].lines], [75, 106, 139])  # reading order

    def test_separate_web_paragraphs_stay_apart(self):
        paras = [P((100, 100, 700, 130), (100, 136, 700, 166)),
                 P((100, 196, 700, 226), (100, 232, 700, 262))]      # 1-line gap
        self.assertEqual(len(merge_touching(paras)), 2)

    def test_overlapping_boxes_merge(self):
        paras = [P((100, 100, 400, 140)), P((300, 120, 600, 160))]
        self.assertEqual(len(merge_touching(paras)), 1)

    def test_max_lines_cap(self):
        a = P(*[(0, i * 32, 500, i * 32 + 30) for i in range(10)])
        b = P(*[(0, 320 + i * 32, 500, 350 + i * 32) for i in range(10)])
        self.assertEqual(len(merge_touching([a, b], max_lines=14)), 2)


class LayoutTests(unittest.TestCase):
    def test_fits_inside_box_when_possible(self):
        it = Item(1, (100, 100, 500, 160), 30, [("ก", "Short text")])
        c = fit_card([it], measure, SCREEN)
        self.assertLessEqual(c.frame[3], 60 + 8 + 0.01)
        self.assertEqual(c.frame[:2], (96, 96))

    def test_widens_before_growing_taller(self):
        it = Item(1, (100, 100, 300, 130), 30, [("ก", "A much longer English translation " * 2)])
        c = fit_card([it], measure, SCREEN)
        self.assertEqual(c.pt, 11)
        self.assertGreater(c.frame[2], 200 + 8)            # wider than the source

    def test_stays_on_screen(self):
        it = Item(1, (1300, 100, 1430, 130), 30, [("ก", "Long translation text " * 4)])
        c = fit_card([it], measure, SCREEN)
        self.assertLessEqual(c.frame[0] + c.frame[2], SCREEN[0] - 4 + 0.01)

    def test_colliding_cards_become_one(self):
        # two chunks close together; the first one's English needs more room
        a = Item(1, (100, 100, 300, 125), 25, [("ก", "This translation is far too long to fit " * 3)])
        b = Item(2, (100, 140, 300, 165), 25, [("ข", "Next")])
        cards = plan([a, b], measure, SCREEN)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].keys, (1, 2))
        self.assertEqual(cards[0].owners, [(1, 0), (2, 0)])

    def test_no_overlaps_in_busy_page(self):
        items = [Item(i, (100 + (i % 3) * 400, 80 + (i // 3) * 70, 420 + (i % 3) * 400,
                          110 + (i // 3) * 70), 30, [("ก", "word " * (5 + 7 * (i % 4)))])
                 for i in range(15)]
        cards = plan(items, measure, SCREEN)
        for i in range(len(cards)):
            for j in range(i + 1, len(cards)):
                self.assertFalse(_overlap(cards[i].frame, cards[j].frame, 0))
        self.assertEqual(sorted(k for c in cards for k in c.keys), list(range(15)))


class GroupAndClampTests(unittest.TestCase):
    def test_group_overlapping(self):
        boxes = {1: (0, 0, 100, 50), 2: (90, 40, 200, 90), 3: (500, 500, 600, 550), 4: (195, 85, 260, 120)}
        self.assertEqual(group_overlapping(boxes), [(1, 2, 4), (3,)])

    def test_card_kept_on_screen_vertically(self):
        top = fit_card([Item(1, (100, -30, 400, 10), 20, [("ก", "Hi")])], measure, SCREEN)
        self.assertGreaterEqual(top.frame[1], 2)
        low = fit_card([Item(1, (100, 880, 400, 920), 20, [("ก", "Hi")])], measure, SCREEN)
        self.assertLessEqual(low.frame[1] + low.frame[3], SCREEN[1] - 2 + 0.01)


if __name__ == "__main__":
    unittest.main(verbosity=2)
