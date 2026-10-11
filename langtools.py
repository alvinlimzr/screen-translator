"""
Language helpers (pure Python, testable anywhere).

- VISION_LANGS: the hint list to give Vision for each target language.
  Vision effectively uses only the FIRST language, so put the target first.
- extract_source(): keep only the part of an OCR line that is in the target
  language, e.g. "2． 私はいつでもいいです。Watashi wa ..." -> "私はいつでもいいです。"
"""
from __future__ import annotations

import re
from typing import Callable, Optional

VISION_LANGS = {
    "en": ["en-US"],
    "ja": ["ja-JP", "en-US"],
    "zh": ["zh-Hans", "en-US"],
    "ko": ["ko-KR", "en-US"],
    "th": ["th-TH", "en-US"],
    "fr": ["fr-FR", "en-US"],
    "es": ["es-ES", "en-US"],
}

LATIN_LETTER = re.compile(r"[A-Za-z]")
NON_LATIN_SCRIPT = re.compile(r"[\u0e00-\u0e7f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")
URLISH = re.compile(r"(https?://|www\.|\.com\b|\.py\b|[_/\\]{2,}|^[\w.-]+@)")

SCRIPT_RE = {
    "ja": re.compile(r"[぀-ヿ㐀-䶿一-鿿々〆ヶ]"),
    "zh": re.compile(r"[㐀-䶿一-鿿]"),
    "ko": re.compile(r"[가-힯ᄀ-ᇿ㄰-㆏]"),
    "th": re.compile(r"[฀-๿]"),
}

NUM = r"[0-9๐-๙฿$€¥%][0-9๐-๙,.:%/฿$€¥\-]*"
NUM_AFTER = re.compile(r"(?:\s*" + NUM + r")+")
NUM_BEFORE = re.compile(r"(" + NUM + r"\s*)$")
LIST_MARKER = re.compile(r"[0-9๐-๙]+\s*[.)．、]\s*")

# Punctuation allowed to trail the extracted span
TRAIL = "。．！？、，」』）〕】…!?.,~ー"


def extract_source(text: str, lang: str,
                   detect: Optional[Callable[[str], Optional[str]]] = None,
                   min_chars: int = 2, min_latin_len: int = 8) -> Optional[str]:
    """Return the target-language part of an OCR line, or None to skip it."""
    rx = SCRIPT_RE.get(lang)
    if rx is not None:
        hits = list(rx.finditer(text))
        if len(hits) < min_chars:
            return None
        start, end = hits[0].start(), hits[-1].end()
        while end < len(text) and text[end] in TRAIL:
            end += 1
        # keep numbers that belong to the sentence ("ลดราคา 30%", "2 ชั่วโมง")
        m = NUM_AFTER.match(text, end)
        if m:
            end = m.end()
        m = NUM_BEFORE.search(text[:start])
        if m and not LIST_MARKER.fullmatch(m.group(1)):
            start = m.start(1)
        return text[start:end].strip()

    t = text.strip()
    if lang == "en":
        return _english(t, detect)

    # Latin-script languages (fr, es): need a language detector
    if len(t) < min_latin_len or detect is None:
        return None
    return t if detect(t) == lang else None


def _english(t: str, detect=None) -> Optional[str]:
    """English source: mostly Latin letters, at least one real word, not a URL/path.
    With a detector (macOS NaturalLanguage), longer lines must also read as English."""
    letters = LATIN_LETTER.findall(t)
    if len(letters) < 3 or NON_LATIN_SCRIPT.search(t) or URLISH.search(t):
        return None
    if len(letters) / max(1, len(t.replace(" ", ""))) < 0.6:
        return None
    if not re.search(r"[A-Za-z]{3,}", t):
        return None
    if detect is not None and len(t) >= 20 and detect(t) not in (None, "en"):
        return None
    return t
