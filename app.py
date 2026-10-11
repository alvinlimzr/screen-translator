#!/usr/bin/env python3
"""
Screen Translator v1.4 - offline on-screen translation for language learners (macOS).

    python app.py            (or build "Screen Translator.app" with ./make_app.sh)

Opens the control window; the overlay runs in the background. Closing the window
keeps the app running: use the 文A menu bar icon or the Dock icon to bring it back.
"""
import os
import sys
import threading

import objc
from AppKit import (NSApplication, NSApplicationActivationPolicyRegular,
                    NSControlStateValueOff, NSControlStateValueOn, NSMenu, NSMenuItem,
                    NSStatusBar, NSVariableStatusItemLength)
from Foundation import NSObject
from PyObjCTools import AppHelper

from cache import DiskCache
from favorites import Favorites
from overlay import Overlay
from settings import LANGUAGES, Settings, data_dir, migrate_old_favourites
from translators import download, make_translator
from ui import ControlWindow, FavouritesWindow, confirm

HERE = os.path.dirname(os.path.abspath(__file__))


class AppController(NSObject):

    @objc.python_method
    def setup(self):
        d = data_dir()
        self.settings_path = os.path.join(d, "settings.json")
        self.settings = Settings.load(self.settings_path)
        fav_path = os.path.join(d, "favorites.json")
        migrate_old_favourites(HERE, fav_path)
        self.favs = Favorites(fav_path)
        self.disk = DiskCache(os.path.join(d, "translations.sqlite"))
        self.downloading = False

        translator = make_translator(self.settings.model)
        missing = not translator.available()
        if missing:                       # run without translation until it's downloaded
            translator = make_translator("none")
        self.overlay = Overlay.alloc().init()
        self.overlay.setup(self.settings, translator, self.favs, self.disk, self.status,
                           self.area_selected)
        self.control = ControlWindow.alloc().init()
        self.control.setup(self)
        self.favwin = FavouritesWindow.alloc().init()
        self.favwin.setup(self.favs)
        self.make_main_menu()
        self.make_status_item()
        self.control.show()
        if missing:
            self.offer_download(self.settings.model)

    # ------------------------------------------------------------ used by the UI
    @objc.python_method
    def change(self, **kw):
        for k, v in kw.items():
            setattr(self.settings, k, v)
        self.settings.validate()
        self.settings.save(self.settings_path)
        self.overlay.apply_settings(set(kw))
        self.update_status_menu()

    @objc.python_method
    def model_available(self, key):
        return make_translator(key).available()

    @objc.python_method
    def choose_model(self, key):
        t = make_translator(key)
        if t.available():
            self.change(model=key)
            self.overlay.set_translator(t)
        else:
            self.offer_download(key)

    @objc.python_method
    def offer_download(self, key):
        if self.downloading:
            return
        from settings import MODELS
        if not confirm(f"Download “{MODELS[key]}”?",
                       "This model isn't on this Mac yet. It's downloaded once (about 1–2 GB), "
                       "then everything runs offline.", "Download"):
            self.control.refresh()
            return
        self.downloading = True

        self.control.set_download(0.0, "Starting download…")

        def run():
            try:
                download(key, progress=lambda f, m: AppHelper.callAfter(self.control.set_download, f, m))
                AppHelper.callAfter(self.download_done, key, None)
            except Exception as e:
                AppHelper.callAfter(self.download_done, key, e)
        threading.Thread(target=run, daemon=True).start()

    @objc.python_method
    def download_done(self, key, err):
        self.downloading = False
        self.control.set_download("done", "")
        if err:
            self.status({"note": f"Download failed (check your internet): {err}"})
        else:
            self.change(model=key)
            self.overlay.set_translator(make_translator(key))
        self.control.refresh()

    @objc.python_method
    def select_area(self):
        self.control.win.orderOut_(None)
        self.overlay.start_select()

    @objc.python_method
    def area_selected(self, rect):
        if rect:
            self.change(roi=rect)
        self.control.refresh()
        self.control.show()

    @objc.python_method
    def set_paused(self, paused):
        self.overlay.set_paused(paused)
        self.update_status_menu()

    @objc.python_method
    def show_favourites(self):
        self.favwin.show()

    @objc.python_method
    def clear_cache(self):
        self.disk.clear()
        self.overlay.reset_language()

    @objc.python_method
    def status(self, st):
        if not hasattr(self, "control"):
            return
        self.control.update_status(st)
        if st.get("favourites_changed"):
            self.favwin.refresh()

    # ------------------------------------------------------------ menus
    @objc.python_method
    def make_main_menu(self):
        bar = NSMenu.alloc().init()
        app_item = NSMenuItem.alloc().init()
        bar.addItem_(app_item)
        m = NSMenu.alloc().init()
        m.addItemWithTitle_action_keyEquivalent_("Show Controls", "showControls:", ",").setTarget_(self)
        m.addItemWithTitle_action_keyEquivalent_("Favourites", "showFavs:", "f").setTarget_(self)
        m.addItem_(NSMenuItem.separatorItem())
        m.addItemWithTitle_action_keyEquivalent_("Hide Screen Translator", "hide:", "h")
        m.addItemWithTitle_action_keyEquivalent_("Quit Screen Translator", "terminate:", "q")
        app_item.setSubmenu_(m)
        win_item = NSMenuItem.alloc().init()
        bar.addItem_(win_item)
        wm = NSMenu.alloc().initWithTitle_("Window")
        wm.addItemWithTitle_action_keyEquivalent_("Close", "performClose:", "w")
        wm.addItemWithTitle_action_keyEquivalent_("Minimize", "performMiniaturize:", "m")
        win_item.setSubmenu_(wm)
        NSApplication.sharedApplication().setMainMenu_(bar)

    @objc.python_method
    def make_status_item(self):
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        self.item.button().setTitle_("文A")
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        def add(title, action):
            it = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
            it.setTarget_(self)
            menu.addItem_(it)
            return it

        add("Show Controls…", "showControls:")
        menu.addItem_(NSMenuItem.separatorItem())
        self.mi_pair = add("", "swapLangs:")
        self.mi_hover = add("Hover", "modeHover:")
        self.mi_all = add("Full overlay", "modeAll:")
        menu.addItem_(NSMenuItem.separatorItem())
        self.mi_area = add("Select area…", "selectArea:")
        self.mi_whole = add("Whole screen", "wholeScreen:")
        self.mi_pause = add("Pause", "togglePause:")
        add("Favourites…", "showFavs:")
        menu.addItem_(NSMenuItem.separatorItem())
        add("Quit", "quitApp:")
        self.item.setMenu_(menu)
        self.update_status_menu()

    @objc.python_method
    def update_status_menu(self):
        if not hasattr(self, "mi_pair"):
            return
        s = self.settings
        self.mi_pair.setTitle_(f"{LANGUAGES[s.source]} → {LANGUAGES[s.target]}   (click to swap)")
        self.mi_hover.setState_(NSControlStateValueOn if s.mode == "hover" else NSControlStateValueOff)
        self.mi_all.setState_(NSControlStateValueOn if s.mode == "all" else NSControlStateValueOff)
        self.mi_pause.setTitle_("Resume" if self.overlay.paused else "Pause")
        self.mi_whole.setEnabled_(bool(s.roi))

    def showControls_(self, sender):
        self.control.show()

    def showFavs_(self, sender):
        self.show_favourites()

    def swapLangs_(self, sender):
        self.change(source=self.settings.target, target=self.settings.source)
        self.control.refresh()

    def modeHover_(self, sender):
        self.change(mode="hover")
        self.control.refresh()

    def modeAll_(self, sender):
        self.change(mode="all")
        self.control.refresh()

    def togglePause_(self, sender):
        self.set_paused(not self.overlay.paused)

    def selectArea_(self, sender):
        self.select_area()

    def wholeScreen_(self, sender):
        self.change(roi=None)
        self.control.refresh()

    def quitApp_(self, sender):
        NSApplication.sharedApplication().terminate_(None)

    # ------------------------------------------------------------ app delegate
    def applicationShouldHandleReopen_hasVisibleWindows_(self, app, visible):
        self.control.show()
        return True

    def applicationWillTerminate_(self, note):
        self.overlay.queue.stop()


def main():
    if sys.platform != "darwin":
        sys.exit("Screen Translator's app runs on macOS.")
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    ctrl = AppController.alloc().init()
    app.setDelegate_(ctrl)
    ctrl.setup()
    print("Screen Translator running. Close the window to keep it in the menu bar; ⌘Q to quit.")
    AppHelper.runEventLoop(installInterrupt=True)


if __name__ == "__main__":
    main()
