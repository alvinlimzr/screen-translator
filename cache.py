"""Translations saved on disk (SQLite), so anything seen before is instant."""
from __future__ import annotations

import sqlite3
import threading
from typing import Dict


class DiskCache:
    def __init__(self, path: str):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        with self.lock:
            self.db.execute("""CREATE TABLE IF NOT EXISTS translations (
                model TEXT, src TEXT, tgt TEXT, source TEXT, result TEXT,
                PRIMARY KEY (model, src, tgt, source))""")
            self.db.commit()

    def load(self, model: str, src: str, tgt: str) -> Dict[str, str]:
        with self.lock:
            rows = self.db.execute("SELECT source, result FROM translations WHERE model=? AND src=? AND tgt=?",
                                   (model, src, tgt)).fetchall()
        return dict(rows)

    def put(self, model: str, src: str, tgt: str, source: str, result: str):
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO translations VALUES (?,?,?,?,?)",
                            (model, src, tgt, source, result))
            self.db.commit()

    def count(self) -> int:
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM translations").fetchone()[0]

    def clear(self):
        with self.lock:
            self.db.execute("DELETE FROM translations")
            self.db.commit()
