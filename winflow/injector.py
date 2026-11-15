from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import time

from .hotkey import (
    INPUT,
    INPUT_KEYBOARD,
    KEYBDINPUT,
    KEYEVENTF_KEYUP,
    MAGIC_EXTRA_INFO,
    VK_LMENU,
    VK_LWIN,
    VK_MENU,
    VK_RMENU,
    VK_RWIN,
    kernel32,
    user32,
)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_MENU = 0x12  # Alt
VK_V = 0x56

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL


def _open_clipboard_retry(retries: int = 8, delay_s: float = 0.015) -> bool:
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(delay_s)
    return False


def get_clipboard_text() -> str | None:
    if not _open_clipboard_retry():
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    encoded = text.encode("utf-16-le") + b"\x00\x00"
    if not _open_clipboard_retry():
        return False
    try:
        user32.EmptyClipboard()
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
        if not h_mem:
            return False
        ptr = kernel32.GlobalLock(h_mem)
        if not ptr:
            kernel32.GlobalFree(h_mem)
            return False
        ctypes.memmove(ptr, encoded, len(encoded))
        kernel32.GlobalUnlock(h_mem)
        if not user32.SetClipboardData(CF_UNICODETEXT, h_mem):
            kernel32.GlobalFree(h_mem)
            return False
        return True
    finally:
        user32.CloseClipboard()


def _wait_for_modifiers_release(timeout_s: float = 0.45) -> None:
    """Ensure physical Alt/Win keys from Alt+X are released before sending Ctrl+V."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        held = bool(
            (user32.GetAsyncKeyState(VK_MENU) & 0x8000)
            or (user32.GetAsyncKeyState(VK_LMENU) & 0x8000)
            or (user32.GetAsyncKeyState(VK_RMENU) & 0x8000)
            or (user32.GetAsyncKeyState(VK_LWIN) & 0x8000)
            or (user32.GetAsyncKeyState(VK_RWIN) & 0x8000)
        )
        if not held:
            return
        time.sleep(0.015)


def _send_ctrl_v() -> None:
    inputs = (INPUT * 4)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].u.ki = KEYBDINPUT(VK_CONTROL, 0, 0, 0, MAGIC_EXTRA_INFO)

    inputs[1].type = INPUT_KEYBOARD
    inputs[1].u.ki = KEYBDINPUT(VK_V, 0, 0, 0, MAGIC_EXTRA_INFO)

    inputs[2].type = INPUT_KEYBOARD
    inputs[2].u.ki = KEYBDINPUT(VK_V, 0, KEYEVENTF_KEYUP, 0, MAGIC_EXTRA_INFO)

    inputs[3].type = INPUT_KEYBOARD
    inputs[3].u.ki = KEYBDINPUT(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0, MAGIC_EXTRA_INFO)

    user32.SendInput(4, inputs, ctypes.sizeof(INPUT))


def paste_text_at_cursor(text: str, restore_clipboard: bool = True) -> bool:
    """Paste text into whatever window currently holds keyboard focus."""
    if not text:
        return False

    previous_clip: str | None = None
    if restore_clipboard:
        try:
            previous_clip = get_clipboard_text()
        except Exception:
            previous_clip = None

    if not set_clipboard_text(text):
        return False

    _wait_for_modifiers_release()
    time.sleep(0.025)
    _send_ctrl_v()

    if restore_clipboard and previous_clip is not None:
        time.sleep(0.14)
        try:
            set_clipboard_text(previous_clip)
        except Exception:
            pass

    return True
