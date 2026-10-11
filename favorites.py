"""Saved sentences, stored as JSON in favorites.json (next to this file)."""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(HERE, "favorites.json")


class Favorites:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        self.items: List[Dict] = []
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.items = json.load(f)
            except (OSError, ValueError):
                self.items = []

    def has(self, source: str) -> bool:
        return any(i["source"] == source for i in self.items)

    def toggle(self, source: str, translation: str, lang: str,
               context: Optional[str] = None, app: Optional[str] = None,
               target: Optional[str] = None) -> bool:
        """Save if new, remove if already saved. Returns True if now saved."""
        if self.has(source):
            self.items = [i for i in self.items if i["source"] != source]
            saved = False
        else:
            self.items.append({
                "source": source, "translation": translation, "lang": lang,
                "target": target,
                "context": context, "app": app,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            saved = True
        self._write()
        return saved

    def delete(self, source: str):
        self.items = [i for i in self.items if i["source"] != source]
        self._write()

    def reload(self):
        self.__init__(self.path)

    def _write(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
