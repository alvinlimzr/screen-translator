"""
Screen capture + OCR on macOS, with no image format round trips:
ScreenCaptureKit gives a CGImage, which goes straight to Vision.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading

import objc
import Quartz as Q


# ---------------------------------------------------------------- capture
def capture_sck(px_per_pt: float, timeout: float = 5.0):
    """Main display WITHOUT this app's windows, at px_per_pt resolution."""
    import ScreenCaptureKit as SCK
    done, out = threading.Event(), {}
    pid = os.getpid()

    def got_image(cg, err):
        out["img"], out["err"] = cg, err
        done.set()

    def got_content(content, err):
        try:
            if content is None:
                raise RuntimeError(f"no shareable content: {err}")
            main_id = Q.CGMainDisplayID()
            display = next((d for d in content.displays() if d.displayID() == main_id),
                           content.displays()[0])
            mine = [w for w in content.windows()
                    if w.owningApplication() is not None
                    and w.owningApplication().processID() == pid]
            filt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, mine)
            cfg = SCK.SCStreamConfiguration.alloc().init()
            cfg.setWidth_(int(display.width() * px_per_pt))
            cfg.setHeight_(int(display.height() * px_per_pt))
            cfg.setShowsCursor_(False)
            SCK.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
                filt, cfg, got_image)
        except Exception as e:
            out["err"] = e
            done.set()

    SCK.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
        False, True, got_content)
    if not done.wait(timeout):
        raise RuntimeError("ScreenCaptureKit timed out")
    if out.get("img") is None:
        raise RuntimeError(f"ScreenCaptureKit failed: {out.get('err')}")
    return out["img"]


def capture_screencapture():
    """Fallback (overlay must be hidden first): `screencapture` -> CGImage."""
    path = os.path.join(tempfile.gettempdir(), "screen_translator_shot.png")
    subprocess.run(["screencapture", "-x", "-m", "-t", "png", path], check=True)
    url = Q.CFURLCreateFromFileSystemRepresentation(None, path.encode(), len(path.encode()), False)
    src = Q.CGImageSourceCreateWithURL(url, None)
    return Q.CGImageSourceCreateImageAtIndex(src, 0, None)


def size(cg):
    return Q.CGImageGetWidth(cg), Q.CGImageGetHeight(cg)


def _draw(cg, w, h, gray, rect=None):
    cs = Q.CGColorSpaceCreateDeviceGray() if gray else Q.CGColorSpaceCreateDeviceRGB()
    bpp = 1 if gray else 4
    info = Q.kCGImageAlphaNone if gray else Q.kCGImageAlphaNoneSkipLast
    ctx = Q.CGBitmapContextCreate(None, w, h, 8, w * bpp, cs, info)
    Q.CGContextSetInterpolationQuality(ctx, Q.kCGInterpolationLow)
    img = cg if rect is None else Q.CGImageCreateWithImageInRect(cg, rect)
    Q.CGContextDrawImage(ctx, Q.CGRectMake(0, 0, w, h), img)
    out = Q.CGBitmapContextCreateImage(ctx)
    return bytes(Q.CGDataProviderCopyData(Q.CGImageGetDataProvider(out)))


THUMB_W, THUMB_H = 160, 100


def thumbnail(cg, w: int = THUMB_W, h: int = THUMB_H) -> bytes:
    """Tiny greyscale copy of the screen for change detection (~1 ms)."""
    return _draw(cg, w, h, gray=True)


def gray(cg):
    """Full-size greyscale bytes of a CGImage (for live motion tracking)."""
    w, h = size(cg)
    return _draw(cg, w, h, gray=True), w, h


def crop(cg, x: float, y: float, w: float, h: float):
    """Sub-image in pixel coordinates (top-left origin)."""
    return Q.CGImageCreateWithImageInRect(cg, Q.CGRectMake(x, y, w, h))


def pixel(cg, x: int, y: int):
    """RGB of one pixel (top-left origin)."""
    b = _draw(cg, 1, 1, gray=False, rect=Q.CGRectMake(x, y, 1, 1))
    return b[0], b[1], b[2]


# ---------------------------------------------------------------- OCR
def recognize(cg, languages, level: str = "accurate"):
    """Vision text recognition on a CGImage.
    Returns [(text, confidence, (x, y, w, h))], boxes normalised, origin bottom-left
    (same format as ocrmac, without its PNG encode/decode per scan)."""
    import Vision
    with objc.autorelease_pool():
        req = Vision.VNRecognizeTextRequest.alloc().init()
        req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate if level == "accurate"
                                 else Vision.VNRequestTextRecognitionLevelFast)
        req.setRecognitionLanguages_(languages)
        req.setUsesLanguageCorrection_(True)
        handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, {})
        ok, err = handler.performRequests_error_([req], None)
        if not ok:
            raise RuntimeError(f"Vision failed: {err}")
        out = []
        for obs in req.results() or []:
            cands = obs.topCandidates_(1)
            if not cands:
                continue
            c = cands[0]
            bb = obs.boundingBox()
            out.append((str(c.string()), float(c.confidence()),
                        (bb.origin.x, bb.origin.y, bb.size.width, bb.size.height)))
        return out
