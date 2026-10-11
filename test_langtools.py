import unittest

from langtools import extract_source


class ExtractTests(unittest.TestCase):
    def test_japanese_line_with_number_and_romaji(self):
        self.assertEqual(
            extract_source("2． 私はいつでもいいです。Watashi wa itsudemo ii desu.", "ja"),
            "私はいつでもいいです。")

    def test_japanese_title_with_romaji(self):
        self.assertEqual(extract_source("いつでも itsudemo", "ja"), "いつでも")

    def test_english_and_noise_skipped(self):
        self.assertIsNone(extract_source("Any time is okay.", "ja"))
        self.assertIsNone(extract_source("ロ", "ja"))              # misread icon
        self.assertIsNone(extract_source("ロ Zed's Folder", "ja"))

    def test_other_scripts(self):
        self.assertEqual(extract_source("1. 你好，世界！ hello", "zh"), "你好，世界！")
        self.assertEqual(extract_source("Login 로그인하기", "ko"), "로그인하기")
        self.assertEqual(extract_source("ข่าว สวัสดีครับ", "th"), "ข่าว สวัสดีครับ")
        self.assertIsNone(extract_source("いつでも", "ko"))

    def test_numbers_kept_list_markers_dropped(self):
        self.assertEqual(extract_source("รองเท้าวิ่งรุ่นใหม่ ลดราคา 30%", "th"), "รองเท้าวิ่งรุ่นใหม่ ลดราคา 30%")
        self.assertEqual(extract_source("2 ชั่วโมงที่แล้ว", "th"), "2 ชั่วโมงที่แล้ว")
        self.assertEqual(extract_source("ราคา 1,250 บาท", "th"), "ราคา 1,250 บาท")
        self.assertEqual(extract_source("ราคา ฿1,250", "th"), "ราคา ฿1,250")
        self.assertEqual(extract_source("1. รัฐบาลประกาศ", "th"), "รัฐบาลประกาศ")
        self.assertEqual(extract_source("2． 私はいつでもいいです。Watashi wa", "ja"), "私はいつでもいいです。")

    def test_english_source(self):
        self.assertEqual(extract_source(" Please press the button below ", "en"),
                         "Please press the button below")
        self.assertEqual(extract_source("Log in", "en"), "Log in")
        self.assertIsNone(extract_source("ข่าว Login", "en"))            # Thai mixed in
        self.assertIsNone(extract_source("https://example.com/page", "en"))
        self.assertIsNone(extract_source("12:45 PM", "en"))
        self.assertIsNone(extract_source("Ok", "en"))
        fr = lambda s: "fr"
        self.assertIsNone(extract_source("Bonjour à tous les amis du monde", "en", fr))

    def test_latin_needs_detector(self):
        fake = lambda s: "fr" if "bonjour" in s.lower() else "en"
        self.assertEqual(extract_source(" Bonjour à tous ", "fr", fake), "Bonjour à tous")
        self.assertIsNone(extract_source("Hello everyone", "fr", fake))
        self.assertIsNone(extract_source("Bonjour", "fr", fake))       # too short
        self.assertIsNone(extract_source("Bonjour à tous", "fr", None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
