from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import math
import queue
import threading
import time
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from .hotkey import LRESULT, kernel32, user32

log = logging.getLogger("winflow.overlay")

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
except Exception:
    shcore = None

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

SW_SHOWNOACTIVATE = 4
SW_HIDE = 0

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
SM_CXSCREEN = 0
SM_CYSCREEN = 1
MONITOR_DEFAULTTONEAREST = 2
MDT_EFFECTIVE_DPI = 0

WM_DESTROY = 0x0002
WM_DISPLAYCHANGE = 0x007E
WM_TIMER = 0x0113
WM_DPICHANGED = 0x02E0
WM_USER_UPDATE = 0x0401
WM_USER_QUIT = 0x0402

PTR_MASK = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
HWND_TOPMOST = wintypes.HWND(-1 & PTR_MASK)

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


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


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
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
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
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.MonitorFromPoint.argtypes = [POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
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

if shcore is not None:
    try:
        shcore.GetDpiForMonitor.argtypes = [
            wintypes.HMONITOR,
            ctypes.c_int,
            ctypes.POINTER(wintypes.UINT),
            ctypes.POINTER(wintypes.UINT),
        ]
        shcore.GetDpiForMonitor.restype = ctypes.HRESULT
    except Exception:
        pass

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

BASE_PILL_W = 300
BASE_PILL_H = 52
ANIM_TIMER_ID = 1
WATCHDOG_TIMER_ID = 2

_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}


def enable_per_monitor_dpi() -> None:
    """Enable Per-Monitor V2 DPI awareness so multi-monitor & scaled displays position accurately."""
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_ssize_t]
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if user32.SetProcessDpiAwarenessContext(-4):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            return
    except Exception:
        pass
    if shcore is not None:
        try:
            shcore.SetProcessDpiAwareness(2)
            return
        except Exception:
            pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def _load_ui_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = ["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _get_active_monitor_work_area_and_dpi() -> tuple[int, int, int, int, float]:
    """
    Return (left, top, right, bottom, dpi_scale) of the work area (excluding taskbar)
    for whichever monitor currently holds the active foreground window or mouse cursor.
    """
    hmon = None
    try:
        fg = user32.GetForegroundWindow()
        if fg and user32.IsWindowVisible(fg):
            cls_buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(fg, cls_buf, 64)
            if cls_buf.value not in _SHELL_CLASSES:
                hmon = user32.MonitorFromWindow(fg, MONITOR_DEFAULTTONEAREST)
    except Exception:
        hmon = None

    if not hmon:
        try:
            pt = POINT()
            if user32.GetCursorPos(ctypes.byref(pt)):
                hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
        except Exception:
            hmon = None

    if hmon:
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            dpi_scale = 1.0
            if shcore is not None:
                try:
                    dx = wintypes.UINT(96)
                    dy = wintypes.UINT(96)
                    if shcore.GetDpiForMonitor(hmon, MDT_EFFECTIVE_DPI, ctypes.byref(dx), ctypes.byref(dy)) == 0:
                        dpi_scale = max(1.0, min(3.0, dx.value / 96.0))
                except Exception:
                    dpi_scale = 1.0
            return (
                mi.rcWork.left,
                mi.rcWork.top,
                mi.rcWork.right,
                mi.rcWork.bottom,
                dpi_scale,
            )

    sw = user32.GetSystemMetrics(SM_CXSCREEN) or 1920
    sh = user32.GetSystemMetrics(SM_CYSCREEN) or 1080
    return (0, 0, sw, sh - 48, 1.0)


class FloatingPillOverlay:
    """
    Per-pixel ARGB layered Win32 floating pill.
    Tracks the active monitor's taskbar-safe work area, scales crisply with Per-Monitor DPI,
    continuously asserts HWND_TOPMOST without stealing focus, and self-heals if DWM resets.
    """

    def __init__(self, get_audio_level: Callable[[], float]) -> None:
        self.get_audio_level = get_audio_level
        self._hwnd: wintypes.HWND | None = None
        self._thread_id: int = 0
        self._wndproc_ref: WNDPROC | None = None
        self._class_registered = False
        self._thread: threading.Thread | None = None
        self._cmd_queue: queue.SimpleQueue[tuple[str, str, float]] = queue.SimpleQueue()
        self._ready = threading.Event()
        self._stopping = False

        # Visual state
        self._state = "hidden"  # hidden, listening, processing, success, error
        self._label = ""
        self._hide_at = 0.0
        self._phase = 0.0
        self._frame_count = 0
        self._bars = [0.20, 0.28, 0.38, 0.30, 0.22]
        self._font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    def _get_font(self, px: int, bold: bool) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        key = (px, bold)
        f = self._font_cache.get(key)
        if f is None:
            f = _load_ui_font(px, bold=bold)
            self._font_cache[key] = f
        return f

    def start(self) -> None:
        enable_per_monitor_dpi()
        self._stopping = False
        self._thread = threading.Thread(
            target=self._window_loop, name="WinFlow-OverlayPill", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=3.0)

    def stop(self) -> None:
        self._stopping = True
        if self._hwnd and user32.PostMessageW(self._hwnd, WM_USER_QUIT, 0, 0):
            return
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_USER_QUIT, 0, 0)

    def show_listening(self, label: str = "Listening…") -> None:
        self._push("listening", label, 0.0)

    def show_processing(self, label: str = "Transcribing…") -> None:
        self._push("processing", label, 0.0)

    def show_toast(self, label: str, is_error: bool = False, duration_s: float = 1.2) -> None:
        self._push("error" if is_error else "success", label, duration_s)

    def hide(self) -> None:
        self._push("hidden", "", 0.0)

    def _push(self, state: str, label: str, duration_s: float) -> None:
        self._cmd_queue.put((state, label, duration_s))
        hwnd = self._hwnd
        if hwnd and user32.PostMessageW(hwnd, WM_USER_UPDATE, 0, 0):
            return
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_USER_UPDATE, 0, 0)

    def _create_layered_hwnd(self) -> wintypes.HWND | None:
        hinst = kernel32.GetModuleHandleW(None)
        class_name = "WinFlowLayeredPillClass"
        if self._wndproc_ref is None:
            self._wndproc_ref = WNDPROC(self._wnd_proc)

        if not self._class_registered:
            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = hinst
            wc.lpszClassName = class_name
            atom = user32.RegisterClassExW(ctypes.byref(wc))
            if atom or ctypes.get_last_error() == 1410:  # ERROR_CLASS_ALREADY_EXISTS
                self._class_registered = True

        ex_style = (
            WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW
            | WS_EX_NOACTIVATE
        )
        hwnd = user32.CreateWindowExW(
            ex_style,
            class_name,
            "WinFlow Pill",
            WS_POPUP,
            0,
            0,
            BASE_PILL_W,
            BASE_PILL_H,
            None,
            None,
            hinst,
            None,
        )
        if hwnd:
            user32.SetTimer(hwnd, WATCHDOG_TIMER_ID, 150, None)
        else:
            log.error("CreateWindowExW for overlay pill failed (WinError=%s)", ctypes.get_last_error())
        return hwnd

    def _recreate_hwnd_if_needed(self) -> bool:
        if self._stopping:
            return False
        if self._hwnd and user32.IsWindow(self._hwnd):
            return True
        log.warning("Overlay HWND invalid — recreating layered pill window")
        self._hwnd = self._create_layered_hwnd()
        return bool(self._hwnd)

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

        if not changed:
            return
        if not self._recreate_hwnd_if_needed():
            return

        assert self._hwnd is not None
        if self._state == "hidden":
            user32.KillTimer(self._hwnd, ANIM_TIMER_ID)
            user32.ShowWindow(self._hwnd, SW_HIDE)
        else:
            self._frame_count = 0
            self._render_frame(force_topmost=True)
            user32.SetTimer(self._hwnd, ANIM_TIMER_ID, 40, None)

    def _paint_pill_bgra(self, pill_w: int, pill_h: int, dpi_scale: float) -> bytes:
        # 2x supersampling for razor-sharp edges & text
        ss = 2
        w2, h2 = pill_w * ss, pill_h * ss
        s = dpi_scale * ss

        img = Image.new("RGBA", (w2, h2), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        pad = max(4, int(round(3.5 * s)))
        shadow_offset = max(2, int(round(1.5 * s)))

        # Crisp drop shadow so pill stands out on bright white windows
        draw.rounded_rectangle(
            (pad, pad + shadow_offset, w2 - pad, h2 - pad + shadow_offset),
            radius=(h2 - 2 * pad) // 2,
            fill=(0, 0, 0, 135),
        )

        border_color = {
            "listening": (255, 92, 92, 235),
            "processing": (96, 145, 255, 235),
            "success": (52, 211, 153, 235),
            "error": (255, 102, 102, 240),
        }.get(self._state, (120, 128, 150, 200))

        # High-contrast obsidian body so pill stands out on dark IDEs too
        draw.rounded_rectangle(
            (pad, pad, w2 - pad, h2 - pad),
            radius=(h2 - 2 * pad) // 2,
            fill=(15, 17, 24, 248),
            outline=border_color,
            width=max(2, int(round(1.8 * s))),
        )

        self._phase += 0.30
        cy = h2 // 2
        left_x = int(round(18 * s))

        if self._state == "listening":
            try:
                raw_lvl = float(self.get_audio_level())
                if math.isnan(raw_lvl) or math.isinf(raw_lvl):
                    raw_lvl = 0.08
            except Exception:
                raw_lvl = 0.08
            lvl = max(0.08, min(1.0, raw_lvl))
            weights = [0.58, 0.88, 1.0, 0.82, 0.54]
            bar_w = max(3, int(round(3.6 * s)))
            bar_step = int(round(6.8 * s))
            for i in range(5):
                wave_mod = 0.14 * math.sin(self._phase + i * 0.9)
                target = max(0.18, min(1.0, lvl * weights[i] * 1.28 + wave_mod))
                self._bars[i] = 0.42 * self._bars[i] + 0.58 * target
                bh = max(bar_w, int(round((6 + self._bars[i] * 18) * s)))
                bx = left_x + i * bar_step
                draw.rounded_rectangle(
                    (bx, cy - bh // 2, bx + bar_w, cy + bh // 2),
                    radius=bar_w // 2,
                    fill=(255, 96, 96, 255),
                )
            text_x = left_x + int(round(41 * s))
        elif self._state == "processing":
            for i in range(3):
                pulse = 0.5 + 0.5 * math.sin(self._phase * 1.4 - i * 0.9)
                r = max(2, int(round((2.6 + pulse * 2.5) * s)))
                alpha = int(145 + pulse * 110)
                cx = left_x + int(round((6 + i * 11) * s))
                draw.ellipse(
                    (cx - r, cy - r, cx + r, cy + r),
                    fill=(112, 158, 255, alpha),
                )
            text_x = left_x + int(round(39 * s))
        elif self._state == "success":
            cx = left_x + int(round(10 * s))
            r = int(round(8.5 * s))
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(46, 198, 130, 255))
            lw = max(2, int(round(2.0 * s)))
            draw.line(
                [
                    (cx - int(round(3.6 * s)), cy + int(round(0.4 * s))),
                    (cx - int(round(1.0 * s)), cy + int(round(3.2 * s))),
                    (cx + int(round(4.2 * s)), cy - int(round(2.6 * s))),
                ],
                fill=(255, 255, 255, 255),
                width=lw,
            )
            text_x = left_x + int(round(27 * s))
        else:
            cx = left_x + int(round(10 * s))
            r = int(round(8.5 * s))
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(239, 88, 88, 255))
            lw = max(2, int(round(2.0 * s)))
            off = int(round(3.2 * s))
            draw.line([(cx - off, cy - off), (cx + off, cy + off)], fill=(255, 255, 255, 255), width=lw)
            draw.line([(cx - off, cy + off), (cx + off, cy - off)], fill=(255, 255, 255, 255), width=lw)
            text_x = left_x + int(round(27 * s))

        label = self._label
        if len(label) > 28:
            label = label[:27] + "…"
        font_px = max(14, int(round((11.5 if self._state == "error" else 13.0) * s)))
        font = self._get_font(font_px, bold=(self._state != "error"))
        draw.text(
            (text_x, cy),
            label,
            font=font,
            fill=(246, 248, 255, 255),
            anchor="lm",
        )

        final_img = img.resize((pill_w, pill_h), Image.Resampling.LANCZOS)
        premult = final_img.convert("RGBa")
        r_ch, g_ch, b_ch, a_ch = premult.split()
        return Image.merge("RGBA", (b_ch, g_ch, r_ch, a_ch)).tobytes()

    def _apply_layered_frame(
        self,
        hwnd: wintypes.HWND,
        bgra_bytes: bytes,
        pos_x: int,
        pos_y: int,
        pill_w: int,
        pill_h: int,
    ) -> bool:
        hdc_screen = user32.GetDC(None)
        if not hdc_screen:
            return False
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        if not hdc_mem:
            user32.ReleaseDC(None, hdc_screen)
            return False

        ok = False
        try:
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = pill_w
            bmi.bmiHeader.biHeight = -pill_h
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0

            bits_ptr = ctypes.c_void_p()
            hbmp = gdi32.CreateDIBSection(
                hdc_mem, ctypes.byref(bmi), 0, ctypes.byref(bits_ptr), None, 0
            )
            if hbmp and bits_ptr.value:
                try:
                    ctypes.memmove(bits_ptr.value, bgra_bytes, len(bgra_bytes))
                    old_obj = gdi32.SelectObject(hdc_mem, hbmp)
                    try:
                        pt_dst = POINT(pos_x, pos_y)
                        sz = SIZE(pill_w, pill_h)
                        pt_src = POINT(0, 0)
                        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

                        ok = bool(
                            user32.UpdateLayeredWindow(
                                hwnd,
                                hdc_screen,
                                ctypes.byref(pt_dst),
                                ctypes.byref(sz),
                                hdc_mem,
                                ctypes.byref(pt_src),
                                0,
                                ctypes.byref(blend),
                                ULW_ALPHA,
                            )
                        )
                    finally:
                        gdi32.SelectObject(hdc_mem, old_obj)
                finally:
                    gdi32.DeleteObject(hbmp)
        finally:
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(None, hdc_screen)
        return ok

    def _render_frame(self, force_topmost: bool = False) -> None:
        if self._state == "hidden":
            return
        if not self._recreate_hwnd_if_needed():
            return
        assert self._hwnd is not None

        work_l, work_t, work_r, work_b, dpi_scale = _get_active_monitor_work_area_and_dpi()
        pill_w = max(240, int(round(BASE_PILL_W * dpi_scale)))
        pill_h = max(44, int(round(BASE_PILL_H * dpi_scale)))
        work_w = max(pill_w, work_r - work_l)

        pos_x = work_l + (work_w - pill_w) // 2
        pos_y = work_b - pill_h - int(round(38 * dpi_scale))
        if pos_y < work_t + 8:
            pos_y = work_t + 8

        bgra_bytes = self._paint_pill_bgra(pill_w, pill_h, dpi_scale)
        ok = self._apply_layered_frame(self._hwnd, bgra_bytes, pos_x, pos_y, pill_w, pill_h)
        if not ok:
            err = ctypes.get_last_error()
            log.warning("UpdateLayeredWindow returned False (WinError=%s) — recreating HWND", err)
            if self._hwnd:
                user32.DestroyWindow(self._hwnd)
            self._hwnd = self._create_layered_hwnd()
            if not self._hwnd:
                return
            if not self._apply_layered_frame(self._hwnd, bgra_bytes, pos_x, pos_y, pill_w, pill_h):
                return
            force_topmost = True

        self._frame_count += 1
        # Explicitly assert HWND_TOPMOST on show and every ~320ms so taskbar / fullscreen / browser overlays never cover the pill
        if force_topmost or (self._frame_count % 8 == 1) or not user32.IsWindowVisible(self._hwnd):
            user32.SetWindowPos(
                self._hwnd,
                HWND_TOPMOST,
                pos_x,
                pos_y,
                pill_w,
                pill_h,
                SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOOWNERZORDER,
            )

    def _wnd_proc(
        self, hwnd: wintypes.HWND, msg: int, wParam: int, lParam: int
    ) -> int:
        try:
            if msg == WM_USER_UPDATE:
                self._drain_commands()
                return 0
            if msg == WM_TIMER:
                if wParam == WATCHDOG_TIMER_ID:
                    if not self._cmd_queue.empty():
                        self._drain_commands()
                    return 0
                if wParam == ANIM_TIMER_ID:
                    if self._hide_at > 0 and time.monotonic() >= self._hide_at:
                        self._state = "hidden"
                        self._hide_at = 0.0
                        user32.KillTimer(hwnd, ANIM_TIMER_ID)
                        user32.ShowWindow(hwnd, SW_HIDE)
                    else:
                        self._render_frame(force_topmost=False)
                    return 0
            if msg in (WM_DISPLAYCHANGE, WM_DPICHANGED):
                if self._state != "hidden":
                    self._render_frame(force_topmost=True)
                return 0
            if msg == WM_USER_QUIT:
                user32.KillTimer(hwnd, ANIM_TIMER_ID)
                user32.KillTimer(hwnd, WATCHDOG_TIMER_ID)
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                if self._stopping:
                    user32.PostMessageW(None, 0x0012, 0, 0)  # WM_QUIT only on shutdown!
                return 0
        except Exception:
            log.exception("Unhandled error in overlay WNDPROC (msg=0x%x)", msg)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wParam, lParam)

    def _window_loop(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        try:
            self._hwnd = self._create_layered_hwnd()
            if self._hwnd:
                log.info("Overlay layered pill initialized (hwnd=0x%x)", self._hwnd)
        finally:
            self._ready.set()

        msg = wintypes.MSG()
        while not self._stopping:
            res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if res <= 0:
                break
            if not msg.hWnd:
                if msg.message == WM_USER_UPDATE:
                    self._drain_commands()
                    continue
                if msg.message == WM_USER_QUIT:
                    break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
