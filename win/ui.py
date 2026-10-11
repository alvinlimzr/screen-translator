"""Control window and favourites window (PySide6, dark theme)."""
from __future__ import annotations

import csv
import importlib.util
import os
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFileDialog,
                               QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QProgressBar,
                               QPushButton, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from settings import LANGUAGES, MODELS, OCR_QUALITY, TRACKING
from win.theme import QSS

LANG_CODES = list(LANGUAGES)
# NLLB needs ctranslate2, which the Windows build leaves out (Hy-MT 4-bit is faster there anyway)
MODEL_KEYS = [k for k in MODELS if k != "nllb" or importlib.util.find_spec("ctranslate2")]
QUALITY_KEYS = list(OCR_QUALITY)
TRACKING_KEYS = list(TRACKING)
MODE_KEYS = ["hover", "all"]


def label(text, name=None, wrap=False):
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def sep():
    f = QFrame()
    f.setObjectName("sep")
    return f


class Segmented(QWidget):
    def __init__(self, titles, on_change):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        for i, t in enumerate(titles):
            b = QPushButton(t)
            b.setCheckable(True)
            b.setObjectName("segL" if i == 0 else ("segR" if i == len(titles) - 1 else "seg"))
            self.group.addButton(b, i)
            lay.addWidget(b)
        self.group.idClicked.connect(on_change)

    def set(self, i):
        self.group.button(i).setChecked(True)


def slider(lo, hi, on_change, width=170):
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(lo, hi)
    s.setFixedWidth(width)
    s.valueChanged.connect(on_change)
    return s


class ControlWindow(QWidget):
    """All settings, applied live and saved. Closing it keeps the app in the tray."""

    def __init__(self, app, icon: QIcon):
        super().__init__()
        self.app = app
        self.setWindowTitle("Screen Translator")
        self.setWindowIcon(icon)
        self.setStyleSheet(QSS)
        self.setFixedWidth(430)
        self._loading = False
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(9)

        def row(name, *widgets, stretch=True):
            h = QHBoxLayout()
            h.setSpacing(8)
            lb = label(name, "dim")
            lb.setFixedWidth(105)
            h.addWidget(lb)
            for w in widgets:
                h.addWidget(w)
            if stretch:
                h.addStretch(1)
            root.addLayout(h)

        # header
        head = QHBoxLayout()
        head.addWidget(label("Screen Translator", "title"))
        head.addStretch(1)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setObjectName("primary")
        self.pause_btn.clicked.connect(lambda: self.app.set_paused(not self.app.engine.paused))
        head.addWidget(self.pause_btn)
        root.addLayout(head)
        self.state = label("Starting…", "dim")
        self.state.setTextFormat(Qt.TextFormat.RichText)
        root.addWidget(self.state)
        root.addWidget(sep())

        # languages
        root.addWidget(label("LANGUAGES", "section"))
        lang = QHBoxLayout()
        self.src = QComboBox()
        self.tgt = QComboBox()
        for c in LANG_CODES:
            self.src.addItem(LANGUAGES[c])
            self.tgt.addItem(LANGUAGES[c])
        swap = QPushButton("⇄")
        swap.setToolTip("Swap languages")
        swap.setFixedWidth(40)
        swap.clicked.connect(self.swap)
        self.src.currentIndexChanged.connect(lambda _i: self.lang_changed(self.src))
        self.tgt.currentIndexChanged.connect(lambda _i: self.lang_changed(self.tgt))
        for w in (label("From", "dim"), self.src, swap, label("To", "dim"), self.tgt):
            lang.addWidget(w)
        root.addLayout(lang)
        root.addWidget(sep())

        # display
        root.addWidget(label("DISPLAY", "section"))
        self.mode = Segmented(["Hover", "Full overlay"], lambda i: self.change(mode=MODE_KEYS[i]))
        row("Mode", self.mode)
        self.outline = QCheckBox("Outline detected text")
        self.outline.toggled.connect(lambda v: self.change(show_outlines=v))
        row("", self.outline)
        self.opacity_lbl = label("", "dim")
        self.opacity = slider(20, 95, lambda v: self.change(opacity=v / 100))
        row("Card opacity", self.opacity, self.opacity_lbl)
        self.size_lbl = label("", "dim")
        self.size_slider = slider(70, 160, lambda v: self.change(text_scale=round(v / 5) * 5 / 100))
        row("Text size", self.size_slider, self.size_lbl)
        area_sel = QPushButton("Select area…")
        area_sel.setToolTip("Drag a rectangle (a comic panel, subtitles, one window). "
                            "Only that area is read: faster and less clutter.")
        area_sel.clicked.connect(self.app.select_area)
        self.area_clear = QPushButton("Whole screen")
        self.area_clear.clicked.connect(lambda: self.change(roi=None))
        row("Area", area_sel, self.area_clear)
        self.area_lbl = label("", "dim")
        row("", self.area_lbl)
        root.addWidget(sep())

        # speed
        root.addWidget(label("SPEED", "section"))
        self.tracking = Segmented([TRACKING[k] for k in TRACKING_KEYS],
                                  lambda i: self.change(tracking=TRACKING_KEYS[i]))
        self.tracking.setToolTip("Live: translations glide with the text as you scroll "
                                 "(experimental, more CPU). Standard: they hide while the page "
                                 "moves and come back when it stops.")
        row("Tracking", self.tracking)
        self.interval_lbl = label("", "dim")
        self.interval = slider(3, 50, lambda v: self.change(scan_interval=v / 10))
        row("Scan every", self.interval, self.interval_lbl)
        self.delay_lbl = label("", "dim")
        self.delay = slider(0, 30, lambda v: self.change(hover_delay=v / 20))
        row("Hover delay", self.delay, self.delay_lbl)
        self.quality = Segmented([OCR_QUALITY[k] for k in QUALITY_KEYS],
                                 lambda i: self.change(ocr_quality=QUALITY_KEYS[i]))
        row("OCR quality", self.quality)
        self.model = QComboBox()
        self.model.setMinimumWidth(250)
        for k in MODEL_KEYS:
            self.model.addItem(MODELS[k])
        self.model.activated.connect(lambda i: self.app.choose_model(MODEL_KEYS[i]))
        row("Model", self.model)
        self.dl_bar = QProgressBar()
        self.dl_bar.setRange(0, 1000)
        self.dl_bar.setTextVisible(False)
        self.dl_lbl = label("", "stats")
        root.addWidget(self.dl_bar)
        root.addWidget(self.dl_lbl)
        self.dl_bar.hide()
        self.dl_lbl.hide()
        root.addWidget(sep())

        # bottom
        bottom = QHBoxLayout()
        self.fav_btn = QPushButton("Favourites")
        self.fav_btn.clicked.connect(self.app.show_favourites)
        clear = QPushButton("Clear cache")
        clear.clicked.connect(self.clear_cache)
        bottom.addWidget(self.fav_btn)
        bottom.addWidget(clear)
        bottom.addStretch(1)
        root.addLayout(bottom)
        self.stats = label("", "stats", wrap=True)
        root.addWidget(self.stats)
        root.addWidget(label("Shift + click a sentence to save it ★ · hold Ctrl to peek at the original. "
                             "Everything runs on this PC; nothing is sent online.", "tip", wrap=True))
        self.refresh()

    # ------------------------------------------------------------ state
    def change(self, **kw):
        if not self._loading:
            self.app.change(**kw)
            self.refresh()

    def refresh(self):
        s = self.app.settings
        self._loading = True
        try:
            self.src.setCurrentIndex(LANG_CODES.index(s.source))
            self.tgt.setCurrentIndex(LANG_CODES.index(s.target))
            self.mode.set(MODE_KEYS.index(s.mode))
            self.outline.setChecked(s.show_outlines)
            self.outline.setEnabled(s.mode == "hover")
            self.opacity.setValue(round(s.opacity * 100))
            self.opacity_lbl.setText(f"{round(s.opacity * 100)}%")
            self.size_slider.setValue(round(s.text_scale * 100))
            self.size_lbl.setText(f"{round(s.text_scale * 100)}%")
            self.interval.setValue(round(s.scan_interval * 10))
            self.interval_lbl.setText(f"{s.scan_interval:.1f} s")
            self.delay.setValue(round(s.hover_delay * 20))
            self.delay_lbl.setText(f"{s.hover_delay:.2f} s")
            self.quality.set(QUALITY_KEYS.index(s.ocr_quality))
            self.tracking.set(TRACKING_KEYS.index(s.tracking))
            for i, k in enumerate(MODEL_KEYS):
                self.model.setItemText(i, MODELS[k] + ("" if self.app.model_available(k) else "  (download)"))
            self.model.setCurrentIndex(MODEL_KEYS.index(s.model))
            self.model.setEnabled(not self.app.downloading)
            if s.roi:
                l, t, r, b = s.roi
                self.area_lbl.setText(f"{round(r - l)} × {round(b - t)} area")
            else:
                self.area_lbl.setText("Whole screen")
            self.area_clear.setEnabled(bool(s.roi))
            self.fav_btn.setText(f"Favourites ({len(self.app.favs.items)})")
        finally:
            self._loading = False

    def update_status(self, st):
        if st.get("note"):
            self.state.setText(st["note"])
            return
        paused = st.get("paused")
        self.pause_btn.setText("Resume" if paused else "Pause")
        dot = lambda c: f'<span style="color:{c}">●</span>&nbsp;&nbsp;'
        if paused:
            self.state.setText(dot("#5d6672") + "Paused")
        elif st.get("loading_model"):
            self.state.setText(dot("rgba(125,211,252,0.6)") + "Loading translation model…")
        else:
            q = st.get("queued", 0)
            self.state.setText(dot("#7DD3FC") + f"Running · {st.get('chunks', 0)} on screen"
                               + (f" · translating {q}" if q else ""))
        ocr, fps = st.get("ocr"), st.get("live_fps")
        scans, skipped = st.get("scans", 0), st.get("skipped", 0)
        checks = scans + skipped
        parts = [f"last OCR {ocr:.2f} s" if ocr else "", f"live tracking {fps:.0f} fps" if fps else "",
                 f"{st.get('cached', 0)} translations cached",
                 f"{round(100 * skipped / checks)}% of checks skipped (screen unchanged)" if checks else "",
                 {"sck": "overlay hidden from capture", "hide": "capture by hiding overlay"}.get(st.get("capture"), "")]
        self.stats.setText(" · ".join(p for p in parts if p))

    def set_download(self, frac, msg):
        if frac == "done":
            self.dl_bar.hide()
            self.dl_lbl.hide()
            self.refresh()
            return
        self.dl_bar.show()
        self.dl_lbl.show()
        if frac is None:
            self.dl_bar.setRange(0, 0)          # busy indicator
        else:
            self.dl_bar.setRange(0, 1000)
            self.dl_bar.setValue(int(frac * 1000))
        self.dl_lbl.setText(("" if frac is None else f"{frac * 100:.0f}% · ") + msg)
        self.model.setEnabled(False)

    # ------------------------------------------------------------ actions
    def lang_changed(self, sender):
        if self._loading:
            return
        s = self.app.settings
        src, tgt = LANG_CODES[self.src.currentIndex()], LANG_CODES[self.tgt.currentIndex()]
        if src == tgt:                        # picking the same language swaps them
            if sender is self.src:
                tgt = s.source
            else:
                src = s.target
        self.change(source=src, target=tgt)

    def swap(self):
        s = self.app.settings
        self.change(source=s.target, target=s.source)

    def clear_cache(self):
        if QMessageBox.question(self, "Clear saved translations?",
                                "Translations are re-created as needed. Favourites are not affected.") \
                == QMessageBox.StandardButton.Yes:
            self.app.clear_cache()

    def show_window(self):
        self.show()
        self.raise_()
        self.activateWindow()


# ================================================================ favourites
COLUMNS = [("source", "Original", 230), ("translation", "Translation", 250),
           ("pair", "Languages", 80), ("saved_at", "Saved", 165)]


class FavouritesWindow(QWidget):
    def __init__(self, favs, icon: QIcon):
        super().__init__()
        self.favs = favs
        self.setWindowTitle("Favourites")
        self.setWindowIcon(icon)
        self.setStyleSheet(QSS)
        self.resize(820, 460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        for i, (_k, _t, w) in enumerate(COLUMNS):
            self.table.setColumnWidth(i, w)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table)
        buttons = QHBoxLayout()
        for title, fn in (("Copy", self.copy_row), ("Delete", self.delete_row),
                          ("Export CSV…", self.export_csv), ("Show file", self.reveal)):
            b = QPushButton(title)
            b.clicked.connect(fn)
            buttons.addWidget(b)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        lay.addWidget(label("Tip: export to CSV to import into Anki or a flashcard app.", "tip"))

    def items(self):
        return list(reversed(self.favs.items))           # newest first

    def reload(self):
        self.favs.reload()
        rows = self.items()
        self.table.setRowCount(len(rows))
        for r, it in enumerate(rows):
            vals = [it.get("source"), it.get("translation"),
                    f"{it.get('lang', '?')} → {it.get('target') or 'en'}", it.get("saved_at")]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(str(v or "")))

    def show_window(self):
        self.reload()
        self.show()
        self.raise_()
        self.activateWindow()

    def selected(self):
        r = self.table.currentRow()
        rows = self.items()
        return rows[r] if 0 <= r < len(rows) else None

    def copy_row(self):
        it = self.selected()
        if it:
            QGuiApplication.clipboard().setText(f"{it['source']}\t{it['translation']}")

    def delete_row(self):
        it = self.selected()
        if it:
            self.favs.delete(it["source"])
            self.reload()

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export favourites", "favourites.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:   # BOM: Excel shows Thai/Japanese
            w = csv.writer(f)
            w.writerow(["original", "translation", "from", "to", "context", "app", "saved_at"])
            for it in self.favs.items:
                w.writerow([it.get("source"), it.get("translation"), it.get("lang"), it.get("target") or "en",
                            it.get("context") or "", it.get("app") or "", it.get("saved_at")])

    def reveal(self):
        if not os.path.exists(self.favs.path):
            self.favs._write()
        if sys.platform == "win32":
            subprocess.run(["explorer", "/select,", os.path.normpath(self.favs.path)])
        elif sys.platform == "darwin":
            subprocess.run(["open", "-R", self.favs.path])
        else:
            subprocess.run(["xdg-open", os.path.dirname(self.favs.path)])
