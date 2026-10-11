"""App settings and data locations (pure Python, testable anywhere)."""
from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, fields
from typing import Optional

LANGUAGES = {"en": "English", "th": "Thai", "ja": "Japanese"}
MODES = {"all": "Full overlay", "hover": "Hover"}
OCR_QUALITY = {"fast": "Fast", "balanced": "Balanced", "best": "Best"}
OCR_SCALE = {"fast": 1.0, "balanced": 1.5, "best": 0.0}   # px per point; 0 = native (2x)
TRACKING = {"standard": "Standard", "live": "Live (beta)"}
MODELS = {"hymt8": "Hy-MT 1.8B · best quality",
          "hymt4": "Hy-MT 1.8B · faster (4-bit)",
          "nllb": "NLLB · fastest, no context"}


def data_dir() -> str:
    """Settings, favourites and cache. Mac: ~/Library/Application Support/...,
    Windows: %APPDATA%\\Screen Translator, else ~/.local/share/..."""
    d = os.environ.get("SCREEN_TRANSLATOR_DATA")
    if not d:
        if sys.platform == "darwin":
            d = os.path.expanduser("~/Library/Application Support/Screen Translator")
        elif sys.platform == "win32":
            d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Screen Translator")
        else:
            d = os.path.expanduser("~/.local/share/screen-translator")
    os.makedirs(d, exist_ok=True)
    return d


@dataclass
class Settings:
    source: str = "th"
    target: str = "en"
    mode: str = "hover"
    show_outlines: bool = True
    opacity: float = 0.5
    text_scale: float = 1.0
    scan_interval: float = 1.0      # min seconds between OCR scans
    hover_delay: float = 0.25
    ocr_quality: str = "balanced"
    model: str = "hymt8"
    tracking: str = "standard"      # "live": follow text ~12x/s between scans (experimental)
    roi: Optional[list] = None      # [left, top, right, bottom] in points; None = whole screen
    launch_paused: bool = False

    def validate(self) -> "Settings":
        d = Settings()
        if self.source not in LANGUAGES:
            self.source = d.source
        if self.target not in LANGUAGES:
            self.target = d.target
        if self.mode not in MODES:
            self.mode = d.mode
        if self.ocr_quality not in OCR_QUALITY:
            self.ocr_quality = d.ocr_quality
        if self.model not in MODELS:
            self.model = d.model
        if self.tracking not in TRACKING:
            self.tracking = d.tracking
        if self.roi is not None:
            try:
                l, t, r, b = (float(v) for v in self.roi)
                self.roi = [l, t, r, b] if r - l >= 20 and b - t >= 20 else None
            except (TypeError, ValueError):
                self.roi = None
        self.show_outlines = bool(self.show_outlines)
        self.launch_paused = bool(self.launch_paused)
        clamp = lambda v, lo, hi, dv: dv if not isinstance(v, (int, float)) else max(lo, min(hi, float(v)))
        self.opacity = clamp(self.opacity, 0.2, 0.95, d.opacity)
        self.text_scale = clamp(self.text_scale, 0.7, 1.6, d.text_scale)
        self.scan_interval = clamp(self.scan_interval, 0.3, 5.0, d.scan_interval)
        self.hover_delay = clamp(self.hover_delay, 0.0, 1.5, d.hover_delay)
        return self

    @classmethod
    def load(cls, path: str) -> "Settings":
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            known = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in raw.items() if k in known}).validate()
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: str):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(tmp, path)


def migrate_old_favourites(project_dir: str, new_path: str):
    """v1.0-1.2 kept favorites.json next to the scripts; move it once."""
    old = os.path.join(project_dir, "favorites.json")
    if os.path.exists(old) and not os.path.exists(new_path):
        shutil.copy2(old, new_path)
