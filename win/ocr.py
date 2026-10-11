"""Offline OCR with RapidOCR (PaddleOCR models on ONNX Runtime), models bundled
in ./ocr_models (Apache-2.0):
  Thai              PP-OCRv5 mobile detector + Thai recogniser
  Japanese/English  PP-OCRv6 small detector + multilingual recogniser
Screen text is never rotated, so the orientation classifier is off (faster,
and it found more lines in testing). Thresholds are a bit looser than default:
on a real Thai comic page that recovered 26 lines instead of 20."""
from __future__ import annotations

import os
import sys
import tempfile
import threading

import numpy as np


def models_dir() -> str:
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "ocr_models")


MODELS = {
    "th": ("ch_PP-OCRv5_det_mobile.onnx", "th_PP-OCRv5_rec_mobile.onnx"),
    "ja": ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx"),
    "en": ("PP-OCRv6_det_small.onnx", "PP-OCRv6_rec_small.onnx"),
}
TUNING = {"Global.use_cls": False, "Global.log_level": "warning", "Global.text_score": 0.4,
          "Det.box_thresh": 0.4, "Det.thresh": 0.2, "Det.unclip_ratio": 1.8,
          # never write into the (read-only) install folder
          "Global.model_root_dir": os.path.join(tempfile.gettempdir(), "screen-translator-ocr")}


class RapidOcrBackend:
    def __init__(self, threads: int = 0):
        self._engines = {}
        self._lock = threading.Lock()
        self.threads = threads

    def _engine(self, lang):
        key = MODELS.get(lang, MODELS["en"])
        with self._lock:
            if key not in self._engines:
                from rapidocr import RapidOCR
                d = models_dir()
                params = dict(TUNING)
                params["Det.model_path"] = os.path.join(d, key[0])
                params["Rec.model_path"] = os.path.join(d, key[1])
                if self.threads:
                    params["EngineConfig.onnxruntime.intra_op_num_threads"] = self.threads
                self._engines[key] = RapidOCR(params=params)
            return self._engines[key]

    def read(self, img: np.ndarray, lang: str):
        """img: BGR/BGRA array -> [(text, (l, t, r, b))] in image pixels."""
        if img.ndim == 3 and img.shape[2] == 4:
            img = np.ascontiguousarray(img[:, :, :3])
        res = self._engine(lang)(img)
        if res is None or res.boxes is None or res.txts is None:
            return []
        out = []
        for quad, txt in zip(res.boxes, res.txts):
            q = np.asarray(quad)
            out.append((str(txt), (float(q[:, 0].min()), float(q[:, 1].min()),
                                   float(q[:, 0].max()), float(q[:, 1].max()))))
        return out
