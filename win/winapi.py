"""
Small Win32 helpers via ctypes. Every function is a safe no-op on other systems,
so the Qt UI can also be run (and tested) on macOS/Linux.
"""
from __future__ import annotations

import sys

IS_WIN = sys.platform == "win32"

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020      # mouse clicks pass through
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000       # clicking us never steals focus from the app you're reading
WS_EX_TOOLWINDOW = 0x00000080       # no taskbar button / Alt-Tab entry
WDA_NONE = 0x0
WDA_EXCLUDEFROMCAPTURE = 0x11       # Windows 10 2004+: left out of every screenshot / capture

VK_SHIFT, VK_CONTROL, VK_MENU, VK_ESCAPE = 0x10, 0x11, 0x12, 0x1B

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    _get = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    _set = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    _get.restype = ctypes.c_ssize_t
    _get.argtypes = [wintypes.HWND, ctypes.c_int]
    _set.restype = ctypes.c_ssize_t
    _set.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short


def _ex(hwnd: int, add: int = 0, remove: int = 0) -> None:
    if not IS_WIN or not hwnd:
        return
    style = _get(hwnd, GWL_EXSTYLE)
    _set(hwnd, GWL_EXSTYLE, (style | add) & ~remove)


def setup_overlay_window(hwnd: int) -> None:
    """Layered, click-through, never activates, hidden from Alt-Tab."""
    _ex(hwnd, add=WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)


def set_click_through(hwnd: int, through: bool) -> None:
    if through:
        _ex(hwnd, add=WS_EX_TRANSPARENT)
    else:
        _ex(hwnd, remove=WS_EX_TRANSPARENT)


def exclude_from_capture(hwnd: int) -> bool:
    """Ask Windows to leave this window out of screen captures. False if unsupported
    (Windows older than 10 version 2004) - the app then hides itself while capturing."""
    if not IS_WIN or not hwnd:
        return False
    return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))


def key_down(vk: int) -> bool | None:
    """Is a key held right now, whichever app has focus? None if unknown (non-Windows)."""
    if not IS_WIN:
        return None
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)
