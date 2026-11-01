"""Embed a foreign HWND into a Qt native host (Windows)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import List, Optional, Tuple

USER32 = ctypes.windll.user32
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_BORDER = 0x00800000
WS_DLGFRAME = 0x00400000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_POPUP = 0x80000000
WS_OVERLAPPEDWINDOW = WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOOLWINDOW = 0x00000080
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SW_SHOW = 5

USER32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
USER32.GetWindowLongW.restype = ctypes.c_long
USER32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
USER32.SetWindowLongW.restype = ctypes.c_long
USER32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
USER32.SetParent.restype = wintypes.HWND
USER32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]
USER32.MoveWindow.restype = wintypes.BOOL
USER32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
USER32.SetWindowPos.restype = wintypes.BOOL
USER32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
USER32.ShowWindow.restype = wintypes.BOOL
USER32.IsWindow.argtypes = [wintypes.HWND]
USER32.IsWindow.restype = wintypes.BOOL
USER32.IsWindowVisible.argtypes = [wintypes.HWND]
USER32.IsWindowVisible.restype = wintypes.BOOL
USER32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
USER32.GetWindowRect.restype = wintypes.BOOL
USER32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
USER32.GetWindowTextW.restype = ctypes.c_int
USER32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
USER32.GetWindowThreadProcessId.restype = wintypes.DWORD
USER32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
USER32.EnumWindows.restype = wintypes.BOOL
USER32.GetParent.argtypes = [wintypes.HWND]
USER32.GetParent.restype = wintypes.HWND


def enum_toplevel_for_pid(pid: int) -> List[Tuple[int, str, int, int]]:
    """Return [(hwnd, title, width, height), ...] for visible top-level windows of pid."""
    found: List[Tuple[int, str, int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lp):  # type: ignore
        proc = wintypes.DWORD()
        USER32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
        if int(proc.value) != int(pid):
            return True
        if not USER32.IsWindowVisible(hwnd):
            return True
        # skip owned/child
        if USER32.GetParent(hwnd):
            return True
        buf = ctypes.create_unicode_buffer(512)
        USER32.GetWindowTextW(hwnd, buf, 512)
        title = buf.value or ""
        rc = wintypes.RECT()
        USER32.GetWindowRect(hwnd, ctypes.byref(rc))
        w = int(rc.right - rc.left)
        h = int(rc.bottom - rc.top)
        if w < 80 or h < 60:
            return True
        found.append((int(hwnd), title, w, h))
        return True

    USER32.EnumWindows(_cb, 0)
    # Prefer Minecraft / LWJGL titles, then largest area
    def score(item: Tuple[int, str, int, int]) -> Tuple[int, int]:
        _hwnd, title, w, h = item
        t = title.lower()
        pref = 0
        if "minecraft" in t or "forge" in t or "lwjgl" in t or "失落" in title:
            pref = 10
        return (pref, w * h)

    found.sort(key=score, reverse=True)
    return found


def pick_game_hwnd(pid: int) -> Optional[int]:
    wins = enum_toplevel_for_pid(pid)
    if not wins:
        return None
    return wins[0][0]


def embed_hwnd(child: int, parent: int, width: int, height: int) -> bool:
    """Reparent child into parent and size to fill."""
    if not USER32.IsWindow(child) or not USER32.IsWindow(parent):
        return False
    style = USER32.GetWindowLongW(child, GWL_STYLE)
    style &= ~(WS_CAPTION | WS_THICKFRAME | WS_BORDER | WS_DLGFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_POPUP)
    style |= WS_CHILD | WS_VISIBLE
    USER32.SetWindowLongW(child, GWL_STYLE, style)
    ex = USER32.GetWindowLongW(child, GWL_EXSTYLE)
    ex &= ~(WS_EX_APPWINDOW)
    ex |= WS_EX_TOOLWINDOW
    USER32.SetWindowLongW(child, GWL_EXSTYLE, ex)
    USER32.SetParent(child, parent)
    USER32.MoveWindow(child, 0, 0, max(1, width), max(1, height), True)
    USER32.SetWindowPos(
        child,
        0,
        0,
        0,
        max(1, width),
        max(1, height),
        SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_SHOWWINDOW,
    )
    USER32.ShowWindow(child, SW_SHOW)
    return True


def fit_hwnd(child: int, width: int, height: int) -> None:
    if not child or not USER32.IsWindow(child):
        return
    USER32.MoveWindow(child, 0, 0, max(1, width), max(1, height), True)


def release_hwnd(child: int) -> None:
    """Detach from parent (best-effort) before killing process."""
    if not child or not USER32.IsWindow(child):
        return
    try:
        USER32.SetParent(child, 0)
    except Exception:
        pass
