from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import math
import queue
import threading
import time
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from .hotkey import LRESULT, kernel32, user32

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

SW_SHOWNOACTIVATE = 4
SW_HIDE = 0
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
SM_CXSCREEN = 0
SM_CYSCREEN = 1

WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_USER_UPDATE = 0x0401
WM_USER_QUIT = 0x0402

WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HANDLE),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
user32.SetTimer.restype = ctypes.c_size_t
user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
user32.KillTimer.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND,
    wintypes.HDC,
    ctypes.POINTER(POINT),
    ctypes.POINTER(SIZE),
    wintypes.HDC,
    ctypes.POINTER(POINT),
    wintypes.DWORD,
    ctypes.POINTER(BLENDFUNCTION),
    wintypes.DWORD,
]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(BITMAPINFO),
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE,
    wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

PILL_W = 286
PILL_H = 50
TIMER_ID = 1


def _load_ui_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        ["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


class FloatingPillOverlay:
    """
    Per-pixel ARGB layered Win32 floating pill (Wispr Flow style).
    Never steals focus from the active typing window and renders smooth 60Hz/25Hz animations.
    """

    def __init__(self, get_audio_level: Callable[[], float]) -> None:
        self.get_audio_level = get_audio_level
        self._hwnd: wintypes.HWND | None = None
        self._wndproc_ref: WNDPROC | None = None
        self._thread: threading.Thread | None = None
        self._cmd_queue: queue.SimpleQueue[tuple[str, str, float]] = queue.SimpleQueue()
        self._ready = threading.Event()

        # Visual state
        self._state = "hidden"  # hidden, listening, processing, success, error
        self._label = ""
        self._hide_at = 0.0
        self._phase = 0.0
        self._bars = [0.18, 0.25, 0.35, 0.28, 0.20]

        self._font_main = _load_ui_font(26, bold=True)
        self._font_sub = _load_ui_font(22, bold=False)

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._window_loop, name="WinFlow-OverlayPill", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def stop(self) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_USER_QUIT, 0, 0)

    def show_listening(self, label: str = "Listening…") -> None:
        self._push("listening", label, 0.0)

    def show_processing(self, label: str = "Polishing…") -> None:
        self._push("processing", label, 0.0)

    def show_toast(self, label: str, is_error: bool = False, duration_s: float = 1.2) -> None:
        self._push("error" if is_error else "success", label, duration_s)

    def hide(self) -> None:
        self._push("hidden", "", 0.0)

    def _push(self, state: str, label: str, duration_s: float) -> None:
        self._cmd_queue.put((state, label, duration_s))
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_USER_UPDATE, 0, 0)

    def _drain_commands(self) -> None:
        changed = False
        while not self._cmd_queue.empty():
            try:
                state, label, duration_s = self._cmd_queue.get_nowait()
                self._state = state
                self._label = label
                self._hide_at = (time.monotonic() + duration_s) if duration_s > 0 else 0.0
                changed = True
            except queue.Empty:
                break

        if not changed or not self._hwnd:
            return

        if self._state == "hidden":
            user32.KillTimer(self._hwnd, TIMER_ID)
            user32.ShowWindow(self._hwnd, SW_HIDE)
        else:
            self._render_frame()
            user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE)
            user32.SetTimer(self._hwnd, TIMER_ID, 40, None)  # 25 FPS smooth pill animation

    def _render_frame(self) -> None:
        if not self._hwnd or self._state == "hidden":
            return

        # 2x supersampling for crisp anti-aliased pill borders and typography
        scale = 2
        w2, h2 = PILL_W * scale, PILL_H * scale
        img = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Outer soft shadow + dark frosted pill body
        pad = 6
        draw.rounded_rectangle(
            (pad, pad + 3, w2 - pad, h2 - pad + 2),
            radius=(h2 - 2 * pad) // 2,
            fill=(0, 0, 0, 95),
        )
        border_color = {
            "listening": (255, 92, 92, 175),
            "processing": (99, 140, 255, 185),
            "success": (62, 207, 142, 185),
            "error": (255, 107, 107, 210),
        }.get(self._state, (90, 95, 110, 150))

        draw.rounded_rectangle(
            (pad, pad, w2 - pad, h2 - pad),
            radius=(h2 - 2 * pad) // 2,
            fill=(20, 21, 27, 240),
            outline=border_color,
            width=3,
        )

        self._phase += 0.28
        cy = h2 // 2
        left_x = 34

        if self._state == "listening":
            lvl = max(0.06, min(1.0, self.get_audio_level()))
            weights = [0.55, 0.85, 1.0, 0.8, 0.5]
            for i in range(5):
                wave_mod = 0.12 * math.sin(self._phase + i * 0.9)
                target = max(0.16, min(1.0, lvl * weights[i] * 1.25 + wave_mod))
                self._bars[i] = 0.45 * self._bars[i] + 0.55 * target
                bh = int(12 + self._bars[i] * 34)
                bx = left_x + i * 13
                draw.rounded_rectangle(
                    (bx, cy - bh // 2, bx + 7, cy + bh // 2),
                    radius=3,
                    fill=(255, 96, 96, 255),
                )
            text_x = left_x + 78
        elif self._state == "processing":
            # Smooth pulsing 3-dot spinner
            for i in range(3):
                pulse = 0.5 + 0.5 * math.sin(self._phase * 1.4 - i * 0.9)
                r = int(5 + pulse * 5)
                alpha = int(130 + pulse * 125)
                cx = left_x + 12 + i * 22
                draw.ellipse(
                    (cx - r, cy - r, cx + r, cy + r),
                    fill=(112, 156, 255, alpha),
                )
            text_x = left_x + 76
        elif self._state == "success":
            cx = left_x + 20
            draw.ellipse((cx - 16, cy - 16, cx + 16, cy + 16), fill=(46, 196, 126, 255))
            draw.line(
                [(cx - 7, cy + 1), (cx - 2, cy + 6), (cx + 8, cy - 5)],
                fill=(255, 255, 255, 255),
                width=4,
            )
            text_x = left_x + 52
        else:
            cx = left_x + 20
            draw.ellipse((cx - 16, cy - 16, cx + 16, cy + 16), fill=(235, 87, 87, 255))
            draw.line([(cx - 6, cy - 6), (cx + 6, cy + 6)], fill=(255, 255, 255, 255), width=4)
            draw.line([(cx - 6, cy + 6), (cx + 6, cy - 6)], fill=(255, 255, 255, 255), width=4)
            text_x = left_x + 52

        label = self._label
        if len(label) > 28:
            label = label[:27] + "…"
        draw.text(
            (text_x, cy),
            label,
            font=self._font_sub if self._state == "error" else self._font_main,
            fill=(244, 246, 252, 250),
            anchor="lm",
        )

        final_img = img.resize((PILL_W, PILL_H), Image.Resampling.LANCZOS)
        # Convert straight RGBA -> premultiplied BGRA for Win32 UpdateLayeredWindow
        premult = final_img.convert("RGBa")
        r_ch, g_ch, b_ch, a_ch = premult.split()
        bgra_bytes = Image.merge("RGBA", (b_ch, g_ch, r_ch, a_ch)).tobytes()

        screen_w = user32.GetSystemMetrics(SM_CXSCREEN)
        screen_h = user32.GetSystemMetrics(SM_CYSCREEN)
        pos_x = (screen_w - PILL_W) // 2
        pos_y = screen_h - PILL_H - 64

        hdc_screen = user32.GetDC(None)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = PILL_W
        bmi.bmiHeader.biHeight = -PILL_H  # top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0

        bits_ptr = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(
            hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(bits_ptr), None, 0
        )
        if hbmp and bits_ptr.value:
            ctypes.memmove(bits_ptr.value, bgra_bytes, len(bgra_bytes))
            old_obj = gdi32.SelectObject(hdc_mem, hbmp)

            pt_dst = POINT(pos_x, pos_y)
            sz = SIZE(PILL_W, PILL_H)
            pt_src = POINT(0, 0)
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

            user32.UpdateLayeredWindow(
                self._hwnd,
                hdc_screen,
                ctypes.byref(pt_dst),
                ctypes.byref(sz),
                hdc_mem,
                ctypes.byref(pt_src),
                0,
                ctypes.byref(blend),
                ULW_ALPHA,
            )
            gdi32.SelectObject(hdc_mem, old_obj)
            gdi32.DeleteObject(hbmp)

        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(None, hdc_screen)

    def _wnd_proc(
        self, hwnd: wintypes.HWND, msg: int, wParam: int, lParam: int
    ) -> int:
        if msg == WM_USER_UPDATE:
            self._drain_commands()
            return 0
        if msg == WM_TIMER and wParam == TIMER_ID:
            if self._hide_at > 0 and time.monotonic() >= self._hide_at:
                self._state = "hidden"
                self._hide_at = 0.0
                user32.KillTimer(hwnd, TIMER_ID)
                user32.ShowWindow(hwnd, SW_HIDE)
            else:
                self._render_frame()
            return 0
        if msg == WM_USER_QUIT:
            user32.KillTimer(hwnd, TIMER_ID)
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostMessageW(None, 0x0012, 0, 0)  # WM_QUIT
            return 0
        return user32.DefWindowProcW(hwnd, msg, wParam, lParam)

    def _window_loop(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        class_name = "WinFlowLayeredPillClass"
        self._wndproc_ref = WNDPROC(self._wnd_proc)

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = hinst
        wc.lpszClassName = class_name
        user32.RegisterClassExW(ctypes.byref(wc))

        ex_style = (
            WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW
            | WS_EX_NOACTIVATE
        )
        self._hwnd = user32.CreateWindowExW(
            ex_style,
            class_name,
            "WinFlow Pill",
            WS_POPUP,
            0,
            0,
            PILL_W,
            PILL_H,
            None,
            None,
            hinst,
            None,
        )
        self._ready.set()
        if not self._hwnd:
            return

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
