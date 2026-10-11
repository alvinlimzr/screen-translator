"""
Screen Translator for Windows - entry point.

    python win_app.py            (or the built ScreenTranslator.exe)

Control window + system-tray icon + the on-screen overlay. Closing the window
keeps it running in the tray; quit from the tray menu.
"""
from __future__ import annotations

import os
import sys
import threading
import time

# Models go to a writable per-user folder (must be set before importing translators).
_LOCAL = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
os.environ.setdefault("SCREEN_TRANSLATOR_MODELS", os.path.join(_LOCAL, "Screen Translator", "models"))

from PySide6.QtCore import QRect, QTimer, Qt                       # noqa: E402
from PySide6.QtGui import QAction, QActionGroup, QIcon              # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget  # noqa: E402

from cache import DiskCache                                         # noqa: E402
from engine import Engine                                           # noqa: E402
from favorites import Favorites                                     # noqa: E402
from settings import LANGUAGES, MODELS, Settings, data_dir          # noqa: E402
from translators import download, make_translator                   # noqa: E402
from win.capture import MssCapture                                  # noqa: E402
from win.ocr import RapidOcrBackend                                 # noqa: E402
from win.overlay import Bridge, Overlay                             # noqa: E402
from win.ui import ControlWindow, FavouritesWindow                  # noqa: E402


def resource(name):
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


class AppController:
    def __init__(self, qapp: QApplication):
        self.qapp = qapp
        d = data_dir()
        self.settings_path = os.path.join(d, "settings.json")
        first_run = not os.path.exists(self.settings_path)
        self.settings = Settings.load(self.settings_path)
        if first_run or self.settings.model == "nllb":
            self.settings.model = "hymt4"         # fast on typical PCs (GPU via Vulkan); switch any time
        self.favs = Favorites(os.path.join(d, "favorites.json"))
        self.disk = DiskCache(os.path.join(d, "translations.sqlite"))
        self.downloading = False
        self.icon = QIcon(resource("icon.png"))

        translator = make_translator(self.settings.model)
        self.missing_model = not translator.available()
        if self.missing_model:
            translator = make_translator("none")  # outlines/OCR work; translation after download

        screen = qapp.primaryScreen()
        geo = screen.geometry()
        self.bridge = Bridge()
        self.capture = MssCapture((geo.width(), geo.height()))
        self.ocr = RapidOcrBackend()
        self.engine = Engine(self.settings, translator, self.disk, self.capture, self.ocr,
                             (geo.width(), geo.height()), self.bridge.post, self.bridge.post_later)
        self.overlay = Overlay(self.engine, self.settings, self.favs, QRect(geo),
                               self.status, self.area_selected, self.favourite_changed)
        self.control = ControlWindow(self, self.icon)
        self.favwin = FavouritesWindow(self.favs, self.icon)
        self.make_tray()
        self.control.show_window()
        QTimer.singleShot(300, self.self_test)
        if self.missing_model:
            QTimer.singleShot(600, lambda: self.offer_download(self.settings.model))

    # ------------------------------------------------------------ capture self-test
    def self_test(self):
        """Is the overlay really left out of screen captures? A magenta square is shown
        at the centre of the screen; if a capture still sees it, hide while capturing."""
        if not self.overlay.capture_excludable:
            return self.finish_self_test(False)
        marker = QWidget(self.overlay)
        marker.setStyleSheet("background: magenta;")
        w, h = self.overlay.width(), self.overlay.height()
        marker.setGeometry(w // 2 - 20, h // 2 - 20, 40, 40)
        marker.show()

        def run():
            time.sleep(0.5)
            ok = False
            try:
                r, g, b = self.capture.pixel(w / 2, h / 2)
                ok = not (r > 180 and g < 100 and b > 180)
            except Exception as e:
                print("capture self-test failed:", e, file=sys.stderr)
            self.bridge.post(lambda: (marker.deleteLater(), self.finish_self_test(ok)))
        threading.Thread(target=run, daemon=True).start()

    def finish_self_test(self, excluded):
        self.capture.excluded = excluded
        if not excluded:                       # older Windows: hide the overlay while capturing
            def hide(on):
                self.bridge.post_wait(lambda: self.overlay.setWindowOpacity(0.0 if on else 1.0))
                if on:
                    time.sleep(0.06)
            self.engine.hide_overlay = hide
        self.engine.start()
        self.status(self.engine.status())

    # ------------------------------------------------------------ used by the UI
    def change(self, **kw):
        for k, v in kw.items():
            setattr(self.settings, k, v)
        self.settings.validate()
        self.settings.save(self.settings_path)
        names = set(kw)
        self.engine.settings_changed(names)
        self.overlay.settings_changed(names)
        self.update_tray()

    def model_available(self, key):
        return make_translator(key).available()

    def choose_model(self, key):
        t = make_translator(key)
        if t.available():
            self.change(model=key)
            self.engine.set_translator(t)
        else:
            self.offer_download(key)
        self.control.refresh()

    def offer_download(self, key):
        if self.downloading:
            return
        ans = QMessageBox.question(self.control, f"Download “{MODELS[key]}”?",
                                   "This translation model isn't on this PC yet. It's downloaded once "
                                   "(about 1–2 GB), then everything runs offline.")
        if ans != QMessageBox.StandardButton.Yes:
            self.control.refresh()
            return
        self.downloading = True
        self.control.set_download(0.0, "Starting download…")

        def run():
            try:
                download(key, engine="llama",
                         progress=lambda f, m: self.bridge.post(self.control.set_download, f, m))
                self.bridge.post(self.download_done, key, None)
            except Exception as e:
                self.bridge.post(self.download_done, key, e)
        threading.Thread(target=run, daemon=True).start()

    def download_done(self, key, err):
        self.downloading = False
        self.control.set_download("done", "")
        if err:
            self.status({"note": f"Download failed (check your internet): {err}"})
        else:
            self.change(model=key)
            self.engine.set_translator(make_translator(key))
        self.control.refresh()

    def set_paused(self, paused):
        self.engine.set_paused(paused)
        self.overlay.set_paused(paused)
        self.update_tray()

    def select_area(self):
        self.control.hide()
        self.overlay.start_select()

    def area_selected(self, rect):
        if rect:
            self.change(roi=rect)
        self.control.refresh()
        self.control.show_window()

    def show_favourites(self):
        self.favwin.show_window()

    def favourite_changed(self):
        self.control.refresh()
        if self.favwin.isVisible():
            self.favwin.reload()

    def clear_cache(self):
        self.disk.clear()
        self.engine.reset()

    def status(self, st):
        self.control.update_status(st)

    # ------------------------------------------------------------ tray
    def make_tray(self):
        self.tray = QSystemTrayIcon(self.icon, self.qapp)
        self.tray.setToolTip("Screen Translator")
        m = QMenu()
        m.addAction("Show controls", self.control.show_window)
        m.addSeparator()
        self.a_pair = m.addAction("", lambda: self.change(source=self.settings.target, target=self.settings.source))
        modes = QActionGroup(m)
        self.a_hover = QAction("Hover", m, checkable=True)
        self.a_all = QAction("Full overlay", m, checkable=True)
        for a, mode in ((self.a_hover, "hover"), (self.a_all, "all")):
            modes.addAction(a)
            a.triggered.connect(lambda _c=False, mode=mode: (self.change(mode=mode), self.control.refresh()))
            m.addAction(a)
        m.addSeparator()
        m.addAction("Select area…", self.select_area)
        self.a_whole = m.addAction("Whole screen", lambda: (self.change(roi=None), self.control.refresh()))
        self.a_pause = m.addAction("Pause", lambda: self.set_paused(not self.engine.paused))
        m.addAction("Favourites…", self.show_favourites)
        m.addSeparator()
        m.addAction("Quit", self.quit)
        self.tray.setContextMenu(m)
        self.tray.activated.connect(
            lambda reason: self.control.show_window()
            if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick)
            else None)
        self.tray_menu = m
        self.tray.show()
        self.update_tray()

    def update_tray(self):
        if not hasattr(self, "a_pair"):
            return
        s = self.settings
        self.a_pair.setText(f"{LANGUAGES[s.source]} → {LANGUAGES[s.target]}   (click to swap)")
        self.a_hover.setChecked(s.mode == "hover")
        self.a_all.setChecked(s.mode == "all")
        self.a_whole.setEnabled(bool(s.roi))
        self.a_pause.setText("Resume" if self.engine.paused else "Pause")

    def quit(self):
        self.engine.stop()
        self.tray.hide()
        self.qapp.quit()


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    qapp = QApplication(sys.argv)
    qapp.setApplicationName("Screen Translator")
    qapp.setQuitOnLastWindowClosed(False)
    ctrl = AppController(qapp)
    qapp.aboutToQuit.connect(ctrl.engine.stop)
    sys.exit(qapp.exec())


if __name__ == "__main__":
    main()
