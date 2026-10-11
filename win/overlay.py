"""
Windows on-screen overlay (PySide6), drawn from the shared Engine/Scene.

  Full overlay  semi-transparent cards over every chunk (overlaps merged)
  Hover         thin outline per chunk group; rest the mouse on it to see the
                translation just below
  Shift-click   save a sentence (Shift held over a card highlights sentences;
                in full-overlay mode the original appears above)
  Hold Ctrl     hide everything to peek at the original

The window is click-through except while Shift is held over a card, never
takes focus, and is excluded from screen capture (WDA_EXCLUDEFROMCAPTURE).
"""
from __future__ import annotations

import html
import sys
import threading
import time

from PySide6.QtCore import (QEasingCurve, QObject, QPoint, QPointF, QPropertyAnimation, QRect, QRectF,
                            Qt, QTimer, QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPen, QTextDocument
from PySide6.QtWidgets import QWidget

from layout import Item, plan
from win import winapi
from win.theme import BLACK, BLUE, FONT

PAD = 4
TIP_PAD_X, TIP_PAD_Y = 10, 7
MIN_PT, MAX_PT, TIP_PT = 11, 26, 14
HOVER_SLOP = 8


def qc(rgb, a=1.0):
    return QColor(rgb[0], rgb[1], rgb[2], int(round(a * 255)))


# ---------------------------------------------------------------- thread bridge
class Bridge(QObject):
    """post(fn, *args) from any thread -> runs on the Qt (UI) thread."""
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(lambda job: job())

    def post(self, fn, *args):
        self.call.emit(lambda: fn(*args))

    def post_later(self, sec, fn):
        self.post(lambda: QTimer.singleShot(int(sec * 1000), fn))

    def post_wait(self, fn, timeout=1.0):
        done = threading.Event()

        def job():
            try:
                fn()
            finally:
                done.set()
        self.call.emit(job)
        done.wait(timeout)


# ---------------------------------------------------------------- text
def build_html(pairs, px, favs, hover_idx=None, shadow=False):
    """[(sentence, translation|None)] -> (html, ranges in plain-text positions)."""
    parts, ranges, pos = [], [], 0
    color = "rgba(0,0,0,200)" if shadow else "#ffffff"
    blue = "rgba(0,0,0,200)" if shadow else f"rgb({BLUE[0]},{BLUE[1]},{BLUE[2]})"
    for i, (src, out) in enumerate(pairs):
        if i:
            parts.append(" ")
            pos += 1
        start = pos
        if out is None:
            t = "translating…"
            parts.append(f'<span style="color:{blue}; font-size:{px * 0.85:.0f}px">{t}</span>')
            pos += len(t)
        else:
            style = []
            if i == hover_idx and not shadow:
                style.append(f"background-color: rgba({BLUE[0]},{BLUE[1]},{BLUE[2]},77)")
            if favs.has(src):
                style.append("text-decoration: underline")
                parts.append(f'<span style="color:{blue}">★ </span>')
                pos += 2
            parts.append(f'<span style="color:{color}; {"; ".join(style)}">{html.escape(out)}</span>')
            pos += len(out)
        ranges.append((start, pos))
    return "".join(parts), ranges


def make_doc(text_html, px, width):
    doc = QTextDocument()
    doc.setDocumentMargin(0)
    f = QFont(FONT)
    f.setPixelSize(max(6, int(px)))
    f.setWeight(QFont.Weight.Medium)
    doc.setDefaultFont(f)
    doc.setHtml(text_html)
    doc.setTextWidth(width)
    return doc


class Card(QWidget):
    """Rounded translucent card with translated text (also used for hover tooltips)."""

    def __init__(self, parent, opacity, tooltip):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.bg_alpha = max(0.9, opacity) if tooltip else opacity
        self.alpha = 0.0
        self.doc = self.shadow = None
        self.text_rect = QRectF()
        self.pairs, self.owners, self.ranges, self.pt = [], [], [], TIP_PT
        self.frame_t = (0, 0, 10, 10)
        self.anim = None
        self.show()

    def fade(self, to, ms=180, then=None):
        a = QVariantAnimation(self)
        a.setStartValue(self.alpha)
        a.setEndValue(to)
        a.setDuration(ms)
        a.valueChanged.connect(self._set_alpha)
        if then:
            a.finished.connect(then)
        a.start()

    def _set_alpha(self, v):
        self.alpha = float(v)
        self.update()

    def move_to(self, frame, ms=0, linear=False):
        self.frame_t = frame
        r = QRect(int(frame[0]), int(frame[1]), int(frame[2]), int(frame[3]))
        if ms <= 0:
            self.setGeometry(r)
            return
        if self.anim:
            self.anim.stop()
        self.anim = QPropertyAnimation(self, b"geometry", self)
        self.anim.setDuration(ms)
        self.anim.setEndValue(r)
        self.anim.setEasingCurve(QEasingCurve.Type.Linear if linear else QEasingCurve.Type.OutCubic)
        self.anim.start()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(self.alpha)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(qc(BLUE, 0.65), 1))
        p.setBrush(qc(BLACK, self.bg_alpha))
        p.drawRoundedRect(r, 6, 6)
        if self.doc:
            p.save()
            p.translate(self.text_rect.x() + 0.8, self.text_rect.y() + 0.8)
            self.shadow.drawContents(p)                 # soft shadow keeps text readable
            p.translate(-0.8, -0.8)
            self.doc.drawContents(p)
            p.restore()

    def hit(self, pos: QPointF):
        if not self.doc:
            return None
        pt = QPointF(pos.x() - self.text_rect.x(), pos.y() - self.text_rect.y())
        idx = self.doc.documentLayout().hitTest(pt, Qt.HitTestAccuracy.FuzzyHit)
        for i, (a, z) in enumerate(self.ranges):
            if a <= idx < z:
                return i
        return None


class Outline(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hot = False
        self.show()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self.hot:
            p.setBrush(qc(BLUE, 0.06))
        p.setPen(QPen(qc(BLUE, 0.95 if self.hot else 0.55), 1.5 if self.hot else 1.0))
        p.drawRoundedRect(r, 4, 4)


class Bubble(QWidget):
    """The original sentence, shown above a card while Shift-hovering (full overlay)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.doc = None
        self.hide()

    def show_text(self, text, near, screen_w):
        self.doc = make_doc(f'<span style="color:white">{html.escape(text)}</span>', 16, 10_000)
        w = min(560, self.doc.idealWidth() + 2)
        self.doc.setTextWidth(w)
        h = self.doc.size().height()
        x = max(8, min(near[0], screen_w - w - 28))
        y = near[1] - h - 18
        if y < 0:
            y = near[1] + near[3] + 6
        self.setGeometry(int(x), int(y), int(w + 20), int(h + 12))
        self.show()
        self.raise_()
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(qc(BLUE, 0.8), 1))
        p.setBrush(qc(BLACK, 0.95))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        if self.doc:
            p.translate(10, 6)
            self.doc.drawContents(p)


class AreaFrame(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setPen(QPen(qc(BLUE, 0.35), 1))
        p.drawRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        f = QFont(FONT)
        f.setPixelSize(10)
        f.setBold(True)
        p.setFont(f)
        label = " translating this area "
        w = p.fontMetrics().horizontalAdvance(label) + 4
        p.fillRect(QRect(0, 0, w, 15), qc(BLUE, 0.75))
        p.setPen(qc(BLACK))
        p.drawText(QRect(0, 0, w, 15), Qt.AlignmentFlag.AlignCenter, label)


class SelectLayer(QWidget):
    """Dims the screen; drag a rectangle to choose the area to translate."""

    def __init__(self, parent, done):
        super().__init__(parent)
        self.done, self.a, self.b = done, None, None
        self.setGeometry(parent.rect())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.show()
        self.raise_()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))
        f = QFont(FONT)
        f.setPixelSize(15)
        f.setBold(True)
        p.setFont(f)
        msg = "  Drag to choose the area to translate  ·  click without dragging to cancel  "
        w = p.fontMetrics().horizontalAdvance(msg) + 8
        box = QRect((self.width() - w) // 2, 60, w, 30)
        p.fillRect(box, qc(BLACK, 0.9))
        p.setPen(QColor("white"))
        p.drawText(box, Qt.AlignmentFlag.AlignCenter, msg)
        if self.a and self.b:
            r = QRect(self.a, self.b).normalized()
            p.fillRect(r, qc(BLUE, 0.10))
            p.setPen(QPen(qc(BLUE), 2))
            p.drawRect(r)

    def mousePressEvent(self, e):
        self.a = self.b = e.position().toPoint()

    def mouseMoveEvent(self, e):
        if self.a:
            self.b = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        r = QRect(self.a, e.position().toPoint()).normalized() if self.a else QRect()
        self.done([r.left(), r.top(), r.right(), r.bottom()] if r.width() >= 20 and r.height() >= 20 else None)


# ================================================================ overlay window
class Overlay(QWidget):
    def __init__(self, engine, settings, favs, geometry: QRect, on_status, on_area, on_favourite):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                 | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        if not winapi.IS_WIN:
            flags |= Qt.WindowType.WindowTransparentForInput
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setGeometry(geometry)
        self.e, self.s, self.favs = engine, settings, favs
        self.on_status, self.on_area, self.on_favourite = on_status, on_area, on_favourite
        self.cards, self.outlines, self.groups = {}, {}, []
        self.over, self.over_since, self.hover = None, 0.0, None
        self.mouse_on, self.peeking, self.selecting = False, False, None
        self.bubble = Bubble(self)
        self.area = AreaFrame(self)
        self.show()
        self.hwnd = int(self.winId()) if winapi.IS_WIN else 0
        winapi.setup_overlay_window(self.hwnd)
        self.capture_excludable = winapi.exclude_from_capture(self.hwnd)
        self.show_area()
        engine.listeners.append(self.on_engine)
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start(33)

    # ------------------------------------------------------------ engine events
    def on_engine(self, kind, payload):
        if kind == "scene":
            self.refresh(animated=payload.get("animated", False))
        elif kind == "motion":
            self.apply_motion(payload)
        elif kind == "translated":
            self.refresh(animated=False, text_only=True)
        elif kind == "reset":
            self.set_hover(None)
            for k in list(self.cards):
                self.remove_card(k, fade=False)
            for k in list(self.outlines):
                self.remove_outline(k)
            self.show_area()
        elif kind == "status":
            self.on_status(payload)

    def settings_changed(self, names):
        if names & {"mode", "show_outlines", "text_scale"}:
            self.set_hover(None)
            for k in list(self.cards):
                self.remove_card(k, fade=False)
            for k in list(self.outlines):
                self.remove_outline(k)
            self.refresh()
        if "opacity" in names:
            for c in self.cards.values():
                c.bg_alpha = max(0.9, self.s.opacity) if self.s.mode == "hover" else self.s.opacity
                c.update()
        if "roi" in names:
            self.show_area()

    def set_paused(self, paused):
        self.setVisible(not paused)
        if not paused:
            winapi.setup_overlay_window(self.hwnd)
            winapi.exclude_from_capture(self.hwnd)

    # ------------------------------------------------------------ drawing
    def measure(self, pairs, px, width):
        key = (tuple(pairs), px, round(width))
        h = self._mcache.get(key)
        if h is None:
            h = make_doc(build_html(pairs, px, self.favs)[0], px, width).size().height()
            self._mcache[key] = h
        return h

    def refresh(self, animated=False, text_only=False):
        self._mcache = {}
        scene = self.e.scene
        if self.s.mode == "all":
            for k in list(self.outlines):
                self.remove_outline(k)
            k_ = self.s.text_scale
            items = []
            for lid in scene.fresh():
                info = scene.labels[lid]
                if info["meta"].get("sentences"):
                    pairs, _ = scene.pairs_for((lid,), self.e.queue.cache)
                    items.append(Item(lid, info["box"], info["meta"].get("line_h", 16) * k_, pairs))
            planned = plan(items, self.measure, (self.width(), self.height()), pad=PAD,
                           min_pt=round(MIN_PT * k_), max_pt=round(MAX_PT * k_))
            keep = set()
            old = {lid for key in self.cards for lid in key}
            for c in planned:
                key = tuple(sorted(c.keys))
                keep.add(key)
                existed = key in self.cards
                if not existed:
                    self.new_card(key, fade=not any(x in old for x in key))
                self.fill(key, c.frame, c.text_frame, c.pt, c.pairs, c.owners,
                          ms=0 if text_only or not existed or not animated else 300)
            for key in list(self.cards):
                if key not in keep:
                    self.remove_card(key)
            return
        # hover mode
        self.groups = scene.groups()
        keys = set(self.groups)
        for key in list(self.outlines):
            if key not in keys or not self.s.show_outlines:
                self.remove_outline(key)
        if self.s.show_outlines and not text_only:
            for key in self.groups:
                self.place_outline(key, animated)
        for key in list(self.cards):
            if key in keys:
                self.layout_tip(key, ms=300 if animated else 0)
            else:
                self.remove_card(key)

    def place_outline(self, key, animated):
        l, t, r, b = self.e.scene.group_box(key)
        rect = QRect(int(l - 3), int(t - 2), int(r - l + 6), int(b - t + 4))
        o = self.outlines.get(key)
        if o is None:
            o = self.outlines[key] = Outline(self)
            o.lower()
        o.hot = self.over is not None and self.over in key
        o.setGeometry(rect)
        o.update()

    def remove_outline(self, key):
        o = self.outlines.pop(key, None)
        if o:
            o.hide()
            o.deleteLater()

    def new_card(self, key, fade=True):
        c = Card(self, self.s.opacity, tooltip=self.s.mode == "hover")
        self.bubble.raise_()
        self.cards[key] = c
        if fade:
            c.fade(1.0)
        else:
            c.alpha = 1.0
        return c

    def remove_card(self, key, fade=True):
        c = self.cards.pop(key, None)
        if not c:
            return
        if self.hover and self.hover[0] == key:
            self.set_hover(None)
        if fade:
            c.fade(0.0, 160, then=lambda: (c.hide(), c.deleteLater()))
        else:
            c.hide()
            c.deleteLater()

    def fill(self, key, frame, text_frame, px, pairs, owners, ms=0):
        c = self.cards[key]
        hover_idx = self.hover[1] if self.hover and self.hover[0] == key else None
        tx, ty, tw, _ = text_frame
        body, c.ranges = build_html(pairs, px, self.favs, hover_idx)
        c.doc = make_doc(body, px, tw)
        c.shadow = make_doc(build_html(pairs, px, self.favs, shadow=True)[0], px, tw)
        th = c.doc.size().height()
        x, y, w, h = frame
        h = max(h, th + 2 * ty)
        c.text_rect = QRectF(tx, ty, tw, th)
        c.pairs, c.owners, c.pt = pairs, owners, px
        c.move_to((x, y, w, h), ms)
        c.update()

    def layout_tip(self, key, ms=0):
        if key not in self.cards or not any(k in self.e.scene.labels for k in key):
            return
        px = round(TIP_PT * self.s.text_scale)
        l, t, r, b = self.e.scene.group_box(key)
        pairs, owners = self.e.scene.pairs_for(key, self.e.queue.cache)
        ideal = make_doc(build_html(pairs, px, self.favs)[0], px, 10_000).idealWidth()
        inner = min(560, max(280, r - l), max(60, ideal + 2))
        th = make_doc(build_html(pairs, px, self.favs)[0], px, inner).size().height()
        w, h = inner + 2 * TIP_PAD_X, th + 2 * TIP_PAD_Y
        x = max(8, min(l, self.width() - w - 8))
        y = b + 6
        if y + h > self.height() - 8:
            y = max(8, t - h - 6)
        self.fill(key, (x, y, w, h), (TIP_PAD_X, TIP_PAD_Y, inner, th), px, pairs, owners, ms)

    def show_tip(self, key):
        if key not in self.cards:
            self.new_card(key)
            self.layout_tip(key)

    def rerender(self, key):
        c = self.cards.get(key)
        if not c:
            return
        if self.s.mode == "all":
            x, y, w, h = c.frame_t
            self.fill(key, c.frame_t, (PAD, PAD, w - 2 * PAD, 0), c.pt, c.pairs, c.owners)
        else:
            self.layout_tip(key)

    def apply_motion(self, deltas):
        """Live tracking: glide cards and outlines with the text (~12 fps)."""
        ms = int(1000 / 12)
        for key, c in self.cards.items():
            ds = [deltas[k] for k in key if k in deltas]
            if ds:
                dx = sum(d[0] for d in ds) / len(ds)
                dy = sum(d[1] for d in ds) / len(ds)
                x, y, w, h = c.frame_t
                c.move_to((x + dx, y + dy, w, h), ms, linear=True)
        for key, o in self.outlines.items():
            ds = [deltas[k] for k in key if k in deltas]
            if ds:
                g = o.geometry()
                o.move(int(g.x() + sum(d[0] for d in ds) / len(ds)), int(g.y() + sum(d[1] for d in ds) / len(ds)))

    # ------------------------------------------------------------ area
    def show_area(self):
        roi = self.s.roi
        if roi:
            l, t, r, b = roi
            self.area.setGeometry(int(l), int(t), int(r - l), int(b - t))
            self.area.show()
            self.area.lower()
        else:
            self.area.hide()

    def start_select(self):
        if self.selecting:
            return
        self.e.selecting = True
        self.set_mouse(True)
        if not self.isVisible():
            self.show()
        self.selecting = SelectLayer(self, self._selected)

    def _selected(self, rect):
        layer, self.selecting = self.selecting, None
        layer.hide()
        layer.deleteLater()
        self.e.selecting = False
        self.set_mouse(False)
        self.on_area(rect)

    # ------------------------------------------------------------ mouse & keys
    def set_mouse(self, on):
        if on == self.mouse_on:
            return
        self.mouse_on = on
        if winapi.IS_WIN:
            winapi.set_click_through(self.hwnd, not on)
        else:                                   # (other platforms: testing only)
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not on)
            self.show()

    @staticmethod
    def held(vk, qt_mod):
        v = winapi.key_down(vk)
        if v is None:
            v = bool(QGuiApplication.queryKeyboardModifiers() & qt_mod)
        return v

    def card_at(self, p: QPoint):
        for key, c in self.cards.items():
            if c.geometry().contains(p):
                return key
        return None

    def group_of(self, lid):
        return next((g for g in self.groups if lid in g), (lid,))

    def poll(self):
        if self.selecting or not self.isVisible():
            return
        peek = self.held(winapi.VK_CONTROL, Qt.KeyboardModifier.ControlModifier)
        if peek != self.peeking:
            self.peeking = peek
            self.setWindowOpacity(0.0 if peek else 1.0)
        if peek:
            return
        now = time.time()
        p = self.mapFromGlobal(QCursor.pos())
        key = self.card_at(p)
        over = self.e.scene.chunk_at(p.x(), p.y(), HOVER_SLOP) if key is None else \
            next((k for k in key if k in self.e.scene.labels), None)
        if over != self.over:
            old, self.over, self.over_since = self.over, over, now
            if over is not None:
                self.e.boost(over)
            for g, o in self.outlines.items():
                hot = over is not None and over in g
                if o.hot != hot:
                    o.hot = hot
                    o.update()
            _ = old
        if self.s.mode == "hover":
            want = self.group_of(over) if over is not None else None
            if want is not None and want not in self.cards and now - self.over_since >= self.s.hover_delay:
                self.show_tip(want)
            for k in list(self.cards):
                if k != want and k != key:
                    self.remove_card(k)
        shift = self.held(winapi.VK_SHIFT, Qt.KeyboardModifier.ShiftModifier)
        hit = None
        if shift and key is not None and key in self.cards:
            c = self.cards[key]
            hit = (key, c.hit(QPointF(p - c.pos())))
        self.set_mouse(hit is not None)
        if hit != self.hover:
            self.set_hover(hit)

    def set_hover(self, hit):
        old, self.hover = self.hover, hit
        if old and old[0] in self.cards and (not hit or hit[0] != old[0]):
            self.rerender(old[0])
        if not hit or hit[1] is None or hit[0] not in self.cards:
            self.bubble.hide()
            if hit and hit[0] in self.cards:
                self.rerender(hit[0])
            return
        key, i = hit
        self.rerender(key)
        if self.s.mode == "all":
            c = self.cards[key]
            self.bubble.show_text(c.pairs[i][0], c.frame_t, self.width())
        else:
            self.bubble.hide()

    def mousePressEvent(self, _e):
        if not self.hover or self.hover[1] is None or self.hover[0] not in self.cards:
            return
        key, i = self.hover
        c = self.cards[key]
        src, out = c.pairs[i]
        if out is None:
            return
        lid = c.owners[i][0]
        info = self.e.scene.labels.get(lid)
        ctx = " ".join(info["meta"].get("sentences", [])) if info else None
        saved = self.favs.toggle(src, out, self.s.source, context=ctx, app=None, target=self.s.target)
        print(("★ saved: " if saved else "☆ removed: ") + f"{src}  ->  {out}", file=sys.stderr)
        self.refresh()
        self.on_favourite()
