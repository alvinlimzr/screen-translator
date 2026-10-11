"""
Control window + favourites window (native AppKit, dark theme:
black background, light-blue accents, white text).
"""
from __future__ import annotations

import csv
import os

import objc
from AppKit import (NSAlert, NSAlertFirstButtonReturn, NSAppearance,
                    NSAppearanceNameDarkAqua, NSBackingStoreBuffered, NSBox,
                    NSBoxSeparator, NSButton, NSColor, NSControlStateValueOff,
                    NSControlStateValueOn, NSFont, NSFontWeightBold,
                    NSFontWeightRegular, NSLayoutAttributeCenterY,
                    NSLayoutAttributeLeading, NSModalResponseOK, NSPasteboard,
                    NSPasteboardTypeString, NSPopUpButton, NSProgressIndicator,
                    NSProgressIndicatorStyleBar, NSSavePanel, NSScrollView,
                    NSSegmentedControl, NSSegmentSwitchTrackingSelectOne, NSSlider,
                    NSStackView, NSSwitch, NSTableColumn, NSTableView, NSTextField,
                    NSUserInterfaceLayoutOrientationHorizontal,
                    NSUserInterfaceLayoutOrientationVertical, NSView, NSWindow,
                    NSWindowStyleMaskClosable, NSWindowStyleMaskMiniaturizable,
                    NSWindowStyleMaskResizable, NSWindowStyleMaskTitled, NSWorkspace)
from Foundation import NSURL, NSMakeRect, NSObject

try:                                    # location differs between PyObjC versions
    from Foundation import NSEdgeInsetsMake
except ImportError:
    try:
        from AppKit import NSEdgeInsetsMake
    except ImportError:
        from AppKit import NSEdgeInsets

        def NSEdgeInsetsMake(t, l, b, r):
            return NSEdgeInsets(t, l, b, r)

from settings import LANGUAGES, MODELS, OCR_QUALITY, TRACKING

BG = (0.055, 0.065, 0.085)
PANEL = (0.09, 0.105, 0.13)
BLUE = (0.49, 0.83, 0.99)
WHITE = (1, 1, 1)
DIM = (0.62, 0.68, 0.76)

LANG_CODES = list(LANGUAGES)
MODEL_KEYS = list(MODELS)
QUALITY_KEYS = list(OCR_QUALITY)
TRACKING_KEYS = list(TRACKING)
MODE_KEYS = ["hover", "all"]


def color(rgb, a=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], a)


# ---------------------------------------------------------------- small builders
def label(text, size=13, rgb=WHITE, weight=NSFontWeightRegular, width=None, wrap=False):
    tf = NSTextField.wrappingLabelWithString_(text) if wrap else NSTextField.labelWithString_(text)
    tf.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
    tf.setTextColor_(color(rgb))
    if width:
        tf.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
        if wrap:
            tf.setPreferredMaxLayoutWidth_(width)
    return tf


def stack(views, vertical=False, spacing=8, insets=None):
    s = NSStackView.stackViewWithViews_(views)
    s.setOrientation_(NSUserInterfaceLayoutOrientationVertical if vertical
                      else NSUserInterfaceLayoutOrientationHorizontal)
    s.setAlignment_(NSLayoutAttributeLeading if vertical else NSLayoutAttributeCenterY)
    s.setSpacing_(spacing)
    if insets:
        s.setEdgeInsets_(NSEdgeInsetsMake(*insets))
    return s


def section(title):
    return label(title.upper(), size=11, rgb=BLUE, weight=NSFontWeightBold)


def separator(width):
    b = NSBox.alloc().initWithFrame_(NSMakeRect(0, 0, width, 1))
    b.setBoxType_(NSBoxSeparator)
    b.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    return b


def popup(titles, target, action, width=130):
    p = NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, width, 26), False)
    p.addItemsWithTitles_(titles)
    p.setTarget_(target)
    p.setAction_(action)
    p.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    return p


def segmented(titles, target, action, width):
    s = NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
        titles, NSSegmentSwitchTrackingSelectOne, target, action)
    s.setSelectedSegmentBezelColor_(color(BLUE, 0.85))
    s.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    return s


def slider(lo, hi, value, target, action, width=170):
    s = NSSlider.sliderWithValue_minValue_maxValue_target_action_(value, lo, hi, target, action)
    s.setContinuous_(True)
    s.setTrackFillColor_(color(BLUE))
    s.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    return s


def button(title, target, action, primary=False):
    b = NSButton.buttonWithTitle_target_action_(title, target, action)
    if primary:
        b.setBezelColor_(color(BLUE, 0.9))
    return b


def dark_window(title, w, h, resizable=False):
    style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable
    if resizable:
        style |= NSWindowStyleMaskResizable
    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, w, h), style, NSBackingStoreBuffered, False)
    win.setTitle_(title)
    win.setReleasedWhenClosed_(False)
    win.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua))
    win.setBackgroundColor_(color(BG))
    return win


# ================================================================ control window
class ControlWindow(NSObject):
    """All user-facing settings. Every change is applied live and saved."""

    @objc.python_method
    def setup(self, app):
        self.app = app                  # AppController: settings, change(), etc.
        s = app.settings
        W = 400
        self.win = dark_window("Screen Translator", W, 600)

        # header
        title = label("Screen Translator", size=20, weight=NSFontWeightBold)
        self.dot = label("●", size=14, rgb=BLUE)
        self.state = label("Starting…", size=12, rgb=DIM)
        self.pause_btn = button("Pause", self, "togglePause:", primary=True)
        spacer = NSView.alloc().init()
        spacer.widthAnchor().constraintGreaterThanOrEqualToConstant_(4).setActive_(True)
        header = stack([title, spacer, self.pause_btn])
        header.widthAnchor().constraintEqualToConstant_(W - 40).setActive_(True)
        state_row = stack([self.dot, self.state], spacing=6)

        # languages
        names = [LANGUAGES[c] for c in LANG_CODES]
        self.src = popup(names, self, "langChanged:", 120)
        self.tgt = popup(names, self, "langChanged:", 120)
        swap = button("⇄", self, "swap:")
        swap.setToolTip_("Swap languages")
        lang_row = stack([label("From", rgb=DIM), self.src, swap, label("To", rgb=DIM), self.tgt],
                         spacing=8)

        # display
        self.mode = segmented(["Hover", "Full overlay"], self, "modeChanged:", 220)
        self.outline = NSSwitch.alloc().init()
        self.outline.setTarget_(self)
        self.outline.setAction_("outlineChanged:")
        self.outline_lbl = label("Outline detected text", width=200)
        self.opacity_lbl = label("", size=12, rgb=DIM, width=44)
        self.opacity = slider(20, 95, s.opacity * 100, self, "opacityChanged:")
        self.size_lbl = label("", size=12, rgb=DIM, width=44)
        self.size = slider(70, 160, s.text_scale * 100, self, "sizeChanged:")

        # speed
        self.interval_lbl = label("", size=12, rgb=DIM, width=44)
        self.interval = slider(0.3, 5.0, s.scan_interval, self, "intervalChanged:")
        self.delay_lbl = label("", size=12, rgb=DIM, width=44)
        self.delay = slider(0.0, 1.5, s.hover_delay, self, "delayChanged:")
        self.quality = segmented([OCR_QUALITY[k] for k in QUALITY_KEYS], self, "qualityChanged:", 220)
        self.model = popup([MODELS[k] for k in MODEL_KEYS], self, "modelChanged:", 240)
        self.tracking = segmented([TRACKING[k] for k in TRACKING_KEYS], self, "trackingChanged:", 220)
        self.tracking.setToolTip_("Live: translations glide with the text as you scroll "
                                  "(experimental, uses more CPU). Standard: they hide while the "
                                  "page moves and come back when it stops.")

        # download progress (hidden unless downloading)
        self.dl_bar = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(0, 0, W - 40, 8))
        self.dl_bar.setStyle_(NSProgressIndicatorStyleBar)
        self.dl_bar.setIndeterminate_(False)
        self.dl_bar.setMinValue_(0.0)
        self.dl_bar.setMaxValue_(1.0)
        self.dl_bar.widthAnchor().constraintEqualToConstant_(W - 40).setActive_(True)
        self.dl_lbl = label("", size=11, rgb=DIM, width=W - 40)
        self.dl_box = stack([self.dl_bar, self.dl_lbl], vertical=True, spacing=4)
        self.dl_box.setHidden_(True)

        # area
        self.area_lbl = label("", size=12, rgb=DIM, width=110)
        area_sel = button("Select area…", self, "selectArea:")
        area_sel.setToolTip_("Drag a rectangle (e.g. a comic panel or video subtitles). "
                             "Only that area is read: faster and less clutter.")
        self.area_clear = button("Whole screen", self, "wholeScreen:")

        # bottom
        self.fav_btn = button("Favourites", self, "showFavourites:")
        clear_btn = button("Clear cache", self, "clearCache:")
        self.stats = label("", size=11, rgb=DIM, width=W - 40, wrap=True)
        tip = label("⌥ Option + click a sentence to save it ★ · hold ⌃ Control to peek at the original. "
                    "Everything runs on this Mac; nothing is sent online.",
                    size=11, rgb=DIM, width=W - 40, wrap=True)

        def row(name, *views):
            return stack([label(name, width=110, rgb=DIM)] + list(views), spacing=8)

        body = [
            header, state_row, separator(W - 40),
            section("Languages"), lang_row, separator(W - 40),
            section("Display"),
            row("Mode", self.mode),
            row("", self.outline, self.outline_lbl),
            row("Card opacity", self.opacity, self.opacity_lbl),
            row("Text size", self.size, self.size_lbl),
            separator(W - 40),
            row("Area", area_sel, self.area_clear),
            row("", self.area_lbl),
            separator(W - 40),
            section("Speed"),
            row("Tracking", self.tracking),
            row("Scan every", self.interval, self.interval_lbl),
            row("Hover delay", self.delay, self.delay_lbl),
            row("OCR quality", self.quality),
            row("Model", self.model),
            self.dl_box,
            separator(W - 40),
            stack([self.fav_btn, clear_btn], spacing=8),
            self.stats, tip,
        ]
        root = stack(body, vertical=True, spacing=10, insets=(20, 20, 20, 20))
        self.win.setContentView_(root)
        self.win.setContentSize_(root.fittingSize())
        self.win.center()
        self.refresh()

    # ------------------------------------------------------------ show state
    @objc.python_method
    def show(self):
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.win.makeKeyAndOrderFront_(None)

    @objc.python_method
    def refresh(self):
        s = self.app.settings
        self.src.selectItemAtIndex_(LANG_CODES.index(s.source))
        self.tgt.selectItemAtIndex_(LANG_CODES.index(s.target))
        self.mode.setSelectedSegment_(MODE_KEYS.index(s.mode))
        self.outline.setState_(NSControlStateValueOn if s.show_outlines else NSControlStateValueOff)
        self.outline.setEnabled_(s.mode == "hover")
        self.outline_lbl.setTextColor_(color(WHITE if s.mode == "hover" else DIM))
        self.opacity.setDoubleValue_(s.opacity * 100)
        self.opacity_lbl.setStringValue_(f"{round(s.opacity * 100)}%")
        self.size.setDoubleValue_(s.text_scale * 100)
        self.size_lbl.setStringValue_(f"{round(s.text_scale * 100)}%")
        self.interval.setDoubleValue_(s.scan_interval)
        self.interval_lbl.setStringValue_(f"{s.scan_interval:.1f} s")
        self.delay.setDoubleValue_(s.hover_delay)
        self.delay_lbl.setStringValue_(f"{s.hover_delay:.2f} s")
        self.quality.setSelectedSegment_(QUALITY_KEYS.index(s.ocr_quality))
        self.tracking.setSelectedSegment_(TRACKING_KEYS.index(s.tracking))
        if s.roi:
            l, t, r, b = s.roi
            self.area_lbl.setStringValue_(f"{round(r - l)} × {round(b - t)} pt area")
        else:
            self.area_lbl.setStringValue_("Whole screen")
        self.area_clear.setEnabled_(bool(s.roi))
        self.model.setEnabled_(not self.app.downloading)
        for i, k in enumerate(MODEL_KEYS):
            title = MODELS[k] + ("" if self.app.model_available(k) else "  (download)")
            self.model.itemAtIndex_(i).setTitle_(title)
        self.model.selectItemAtIndex_(MODEL_KEYS.index(s.model))
        self.fav_btn.setTitle_(f"Favourites ({len(self.app.favs.items)})")

    @objc.python_method
    def update_status(self, st):
        if st.get("favourites_changed"):
            self.fav_btn.setTitle_(f"Favourites ({len(self.app.favs.items)})")
            return
        if st.get("note"):
            self.state.setStringValue_(st["note"])
            return
        paused = st.get("paused")
        self.pause_btn.setTitle_("Resume" if paused else "Pause")
        if paused:
            self.dot.setTextColor_(color(DIM))
            self.state.setStringValue_("Paused")
        elif st.get("loading_model"):
            self.dot.setTextColor_(color(BLUE, 0.6))
            self.state.setStringValue_("Loading translation model…")
        else:
            self.dot.setTextColor_(color(BLUE))
            q = st.get("queued", 0)
            self.state.setStringValue_(f"Running · {st.get('chunks', 0)} on screen"
                                       + (f" · translating {q}" if q else ""))
        ocr = st.get("ocr")
        fps = st.get("live_fps")
        scans, skipped = st.get("scans", 0), st.get("skipped", 0)
        checks = scans + skipped
        saved = f"{round(100 * skipped / checks)}% of checks skipped (screen unchanged)" if checks else ""
        cap = {"sck": "flicker-free capture", "hide": "capture by hiding overlay"}.get(st.get("capture"), "")
        parts = [f"last OCR {ocr:.2f} s" if ocr else "", f"live tracking {fps:.0f} fps" if fps else "",
                 f"{st.get('cached', 0)} translations cached", saved, cap]
        self.stats.setStringValue_(" · ".join(p for p in parts if p))

    @objc.python_method
    def set_download(self, frac, msg):
        """frac: 0..1, None = size unknown (spinning bar), or 'done' to hide."""
        if frac == "done":
            self.dl_box.setHidden_(True)
            self.dl_bar.stopAnimation_(None)
            self.refresh()
            self.win.setContentSize_(self.win.contentView().fittingSize())
            return
        self.dl_box.setHidden_(False)
        if frac is None:
            self.dl_bar.setIndeterminate_(True)
            self.dl_bar.startAnimation_(None)
        else:
            self.dl_bar.setIndeterminate_(False)
            self.dl_bar.setDoubleValue_(frac)
        self.dl_lbl.setStringValue_(("" if frac is None else f"{frac * 100:.0f}% · ") + msg)
        self.model.setEnabled_(False)
        self.win.setContentSize_(self.win.contentView().fittingSize())

    # ------------------------------------------------------------ actions
    def langChanged_(self, sender):
        s = self.app.settings
        src = LANG_CODES[self.src.indexOfSelectedItem()]
        tgt = LANG_CODES[self.tgt.indexOfSelectedItem()]
        if src == tgt:                    # picking the same language swaps them
            if sender is self.src:
                tgt = s.source
            else:
                src = s.target
        self.app.change(source=src, target=tgt)
        self.refresh()

    def swap_(self, sender):
        s = self.app.settings
        self.app.change(source=s.target, target=s.source)
        self.refresh()

    def modeChanged_(self, sender):
        self.app.change(mode=MODE_KEYS[sender.selectedSegment()])
        self.refresh()

    def outlineChanged_(self, sender):
        self.app.change(show_outlines=sender.state() == NSControlStateValueOn)

    def opacityChanged_(self, sender):
        self.app.change(opacity=round(sender.doubleValue()) / 100)
        self.opacity_lbl.setStringValue_(f"{round(sender.doubleValue())}%")

    def sizeChanged_(self, sender):
        self.app.change(text_scale=round(sender.doubleValue() / 5) * 5 / 100)
        self.size_lbl.setStringValue_(f"{round(sender.doubleValue() / 5) * 5}%")

    def intervalChanged_(self, sender):
        v = round(sender.doubleValue(), 1)
        self.app.change(scan_interval=v)
        self.interval_lbl.setStringValue_(f"{v:.1f} s")

    def delayChanged_(self, sender):
        v = round(sender.doubleValue() * 20) / 20
        self.app.change(hover_delay=v)
        self.delay_lbl.setStringValue_(f"{v:.2f} s")

    def qualityChanged_(self, sender):
        self.app.change(ocr_quality=QUALITY_KEYS[sender.selectedSegment()])

    def trackingChanged_(self, sender):
        self.app.change(tracking=TRACKING_KEYS[sender.selectedSegment()])

    def selectArea_(self, sender):
        self.app.select_area()

    def wholeScreen_(self, sender):
        self.app.change(roi=None)
        self.refresh()

    def modelChanged_(self, sender):
        self.app.choose_model(MODEL_KEYS[sender.indexOfSelectedItem()])
        self.refresh()

    def togglePause_(self, sender):
        self.app.set_paused(not self.app.overlay.paused)

    def showFavourites_(self, sender):
        self.app.show_favourites()

    def clearCache_(self, sender):
        if confirm("Clear saved translations?",
                   "Translations are re-created as needed. Favourites are not affected.", "Clear"):
            self.app.clear_cache()


def confirm(title, text, ok="OK"):
    a = NSAlert.alloc().init()
    a.setMessageText_(title)
    a.setInformativeText_(text)
    a.addButtonWithTitle_(ok)
    a.addButtonWithTitle_("Cancel")
    return a.runModal() == NSAlertFirstButtonReturn


# ================================================================ favourites window
COLUMNS = [("source", "Original", 230), ("translation", "Translation", 260),
           ("pair", "Languages", 80), ("app", "Seen in", 100), ("saved_at", "Saved", 130)]


class FavouritesWindow(NSObject):

    @objc.python_method
    def setup(self, favs):
        self.favs = favs
        self.win = dark_window("Favourites", 840, 460, resizable=True)
        self.table = NSTableView.alloc().initWithFrame_(NSMakeRect(0, 0, 800, 360))
        for ident, title, width in COLUMNS:
            col = NSTableColumn.alloc().initWithIdentifier_(ident)
            col.setTitle_(title)
            col.setWidth_(width)
            self.table.addTableColumn_(col)
        self.table.setUsesAlternatingRowBackgroundColors_(True)
        self.table.setDataSource_(self)
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 800, 360))
        scroll.setDocumentView_(self.table)
        scroll.setHasVerticalScroller_(True)
        scroll.widthAnchor().constraintGreaterThanOrEqualToConstant_(800).setActive_(True)
        scroll.heightAnchor().constraintGreaterThanOrEqualToConstant_(340).setActive_(True)
        buttons = stack([button("Copy", self, "copyRow:"), button("Delete", self, "deleteRow:"),
                         button("Export CSV…", self, "exportCSV:"),
                         button("Show file", self, "reveal:")], spacing=8)
        hint = label("Tip: export to CSV to import into Anki or a flashcard app.", size=11, rgb=DIM)
        root = stack([scroll, buttons, hint], vertical=True, spacing=10, insets=(16, 16, 16, 16))
        self.win.setContentView_(root)
        self.win.setContentSize_(root.fittingSize())

    @objc.python_method
    def show(self):
        from AppKit import NSApplication
        self.favs.reload()
        self.table.reloadData()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.win.center()
        self.win.makeKeyAndOrderFront_(None)

    @objc.python_method
    def refresh(self):
        if self.win.isVisible():
            self.table.reloadData()

    # data source
    def numberOfRowsInTableView_(self, tv):
        return len(self.favs.items)

    def tableView_objectValueForTableColumn_row_(self, tv, col, row):
        items = list(reversed(self.favs.items))          # newest first
        if row >= len(items):
            return ""
        it = items[row]
        ident = col.identifier()
        if ident == "pair":
            return f"{it.get('lang', '?')} → {it.get('target') or 'en'}"
        return str(it.get(ident) or "")

    @objc.python_method
    def selected(self):
        row = self.table.selectedRow()
        items = list(reversed(self.favs.items))
        return items[row] if 0 <= row < len(items) else None

    def copyRow_(self, sender):
        it = self.selected()
        if it:
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            pb.setString_forType_(f"{it['source']}\t{it['translation']}", NSPasteboardTypeString)

    def deleteRow_(self, sender):
        it = self.selected()
        if it:
            self.favs.delete(it["source"])
            self.table.reloadData()

    def exportCSV_(self, sender):
        panel = NSSavePanel.savePanel()
        panel.setNameFieldStringValue_("favourites.csv")
        if panel.runModal() != NSModalResponseOK:
            return
        path = panel.URL().path()
        with open(path, "w", encoding="utf-8-sig", newline="") as f:      # BOM: Excel shows Thai/Japanese
            w = csv.writer(f)
            w.writerow(["original", "translation", "from", "to", "context", "app", "saved_at"])
            for it in self.favs.items:
                w.writerow([it.get("source"), it.get("translation"), it.get("lang"),
                            it.get("target") or "en", it.get("context") or "", it.get("app") or "",
                            it.get("saved_at")])

    def reveal_(self, sender):
        if not os.path.exists(self.favs.path):
            self.favs._write()
        NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_(
            [NSURL.fileURLWithPath_(self.favs.path)])
