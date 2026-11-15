from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import queue
import threading
import time
from typing import Callable

log = logging.getLogger("winflow.hotkey")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32
LRESULT = ctypes.c_ssize_t

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012

LLKHF_ALTDOWN = 0x0020

VK_MENU = 0x12
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_CONTROL = 0x11
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_SHIFT = 0x10
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LWIN = 0x5B
VK_RWIN = 0x5C

VK_X = 0x58
VK_ESCAPE = 0x1B
# Unassigned VK code used by AutoHotkey (#MenuMaskKey vkE8) so releasing Alt
# after Alt+X never highlights or focuses the active window's top menu bar.
VK_MENU_MASK = 0xE8

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
MAGIC_EXTRA_INFO = 0x57464C57  # "WFLW"


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p

user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT

user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL

user32.GetMessageW.argtypes = [
    ctypes.POINTER(wintypes.MSG),
    wintypes.HWND,
    wintypes.UINT,
    wintypes.UINT,
]
user32.GetMessageW.restype = wintypes.BOOL

user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def _send_alt_menu_mask() -> None:
    """Inject a silent unassigned key tap so releasing Alt does not focus the app menu bar."""
    inputs = (INPUT * 2)()
    inputs[0].type = INPUT_KEYBOARD
    inputs[0].u.ki = KEYBDINPUT(VK_MENU_MASK, 0, 0, 0, MAGIC_EXTRA_INFO)
    inputs[1].type = INPUT_KEYBOARD
    inputs[1].u.ki = KEYBDINPUT(VK_MENU_MASK, 0, KEYEVENTF_KEYUP, 0, MAGIC_EXTRA_INFO)
    user32.SendInput(2, inputs, ctypes.sizeof(INPUT))


class AltXHotkeyListener:
    """
    Intercepts Alt + X natively via WH_KEYBOARD_LL (leaving Win + H 100% untouched for
    native Windows Voice Typing). Supports Smart Hybrid (Tap to toggle / Hold to push-to-talk),
    Toggle-only, Hold-only, and Escape to cancel an active recording.
    """

    def __init__(
        self,
        on_start: Callable[[str], None],
        on_stop: Callable[[], None],
        on_cancel: Callable[[], None],
        on_mode_changed: Callable[[str], None] | None = None,
        get_hotkey_mode: Callable[[], str] = lambda: "hybrid",
        get_hold_threshold_ms: Callable[[], int] = lambda: 350,
        is_paused: Callable[[], bool] = lambda: False,
    ) -> None:
        self.on_start = on_start
        self.on_stop = on_stop
        self.on_cancel = on_cancel
        self.on_mode_changed = on_mode_changed
        self.get_hotkey_mode = get_hotkey_mode
        self.get_hold_threshold_ms = get_hold_threshold_ms
        self.is_paused = is_paused

        self._event_queue: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self._hook_handle: ctypes.c_void_p | None = None
        self._hook_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._hook_thread_id: int = 0
        self._hook_proc_ref: HOOKPROC | None = None
        self._running = False

        # Modifier tracking
        self._lalt_down = False
        self._ralt_down = False
        self._alt_down_ts = 0.0
        self._ctrl_down = False
        self._shift_down = False
        self._win_down = False

        self._swallowing_x = False
        self._press_time = 0.0
        # States: "idle", "pending_hybrid", "holding", "toggled"
        self._state = "idle"
        self._lock = threading.Lock()

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._state in ("pending_hybrid", "holding", "toggled")

    def reset_state(self) -> None:
        with self._lock:
            self._state = "idle"

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._dispatch_loop, name="WinFlow-HotkeyWorker", daemon=True
        )
        self._worker_thread.start()
        self._hook_thread = threading.Thread(
            target=self._hook_loop, name="WinFlow-KeyboardHook", daemon=True
        )
        self._hook_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._hook_thread_id:
            user32.PostThreadMessageW(self._hook_thread_id, WM_QUIT, 0, 0)
        self._event_queue.put(("quit", ""))

    def _is_pure_alt_down(self, flags: int) -> bool:
        """True when Alt is held WITHOUT Ctrl, Shift, or Win."""
        if self._ctrl_down or self._shift_down or self._win_down:
            return False

        if bool(flags & LLKHF_ALTDOWN):
            return True

        if self._lalt_down or self._ralt_down:
            if (time.monotonic() - self._alt_down_ts) < 10.0:
                return True
            alt_async = bool(
                (user32.GetAsyncKeyState(VK_MENU) & 0x8000)
                or (user32.GetAsyncKeyState(VK_LMENU) & 0x8000)
                or (user32.GetAsyncKeyState(VK_RMENU) & 0x8000)
            )
            if not alt_async:
                self._lalt_down = False
                self._ralt_down = False
                return False
            return True

        return bool(
            (user32.GetAsyncKeyState(VK_MENU) & 0x8000)
            or (user32.GetAsyncKeyState(VK_LMENU) & 0x8000)
            or (user32.GetAsyncKeyState(VK_RMENU) & 0x8000)
        )

    def _dispatch_loop(self) -> None:
        while self._running:
            action, payload = self._event_queue.get()
            if action == "quit":
                break
            try:
                if action == "start":
                    self.on_start(payload)
                elif action == "mode":
                    if self.on_mode_changed:
                        self.on_mode_changed(payload)
                elif action == "stop":
                    self.on_stop()
                elif action == "cancel":
                    self.on_cancel()
            except Exception:
                pass

    def _handle_chord_release(self) -> None:
        with self._lock:
            now = time.monotonic()
            if self._state == "pending_hybrid":
                threshold_s = max(0.15, self.get_hold_threshold_ms() / 1000.0)
                if (now - self._press_time) < threshold_s:
                    self._state = "toggled"
                    self._event_queue.put(("mode", "toggled"))
                else:
                    self._state = "idle"
                    self._event_queue.put(("stop", ""))
            elif self._state == "holding":
                self._state = "idle"
                self._event_queue.put(("stop", ""))

    def _low_level_proc(self, nCode: int, wParam: int, lParam: int) -> int:
        if nCode >= 0 and not self.is_paused():
            kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if kb.dwExtraInfo != MAGIC_EXTRA_INFO:
                vk = kb.vkCode
                is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
                is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)

                if vk in (VK_LMENU, VK_MENU):
                    if is_down and not self._lalt_down:
                        self._alt_down_ts = time.monotonic()
                    self._lalt_down = is_down
                    if is_up and self._swallowing_x:
                        self._handle_chord_release()
                elif vk == VK_RMENU:
                    if is_down and not self._ralt_down:
                        self._alt_down_ts = time.monotonic()
                    self._ralt_down = is_down
                    if is_up and self._swallowing_x:
                        self._handle_chord_release()
                elif vk in (VK_CONTROL, VK_LCONTROL, VK_RCONTROL):
                    self._ctrl_down = is_down
                elif vk in (VK_SHIFT, VK_LSHIFT, VK_RSHIFT):
                    self._shift_down = is_down
                elif vk in (VK_LWIN, VK_RWIN):
                    self._win_down = is_down
                elif vk == VK_X:
                    if is_down:
                        if (
                            self._swallowing_x
                            and self._state == "idle"
                            and (time.monotonic() - self._press_time) > 3.0
                        ):
                            self._swallowing_x = False

                        if self._swallowing_x:
                            # Swallow OS auto-repeat while holding X
                            if self._state == "pending_hybrid":
                                threshold_s = max(0.15, self.get_hold_threshold_ms() / 1000.0)
                                if (time.monotonic() - self._press_time) >= threshold_s:
                                    with self._lock:
                                        if self._state == "pending_hybrid":
                                            self._state = "holding"
                                            self._event_queue.put(("mode", "holding"))
                            return 1

                        if self._is_pure_alt_down(kb.flags):
                            self._swallowing_x = True
                            # Suppress Alt menu-bar highlight on release
                            _send_alt_menu_mask()
                            mode = self.get_hotkey_mode()
                            with self._lock:
                                if self._state == "toggled":
                                    self._state = "idle"
                                    self._event_queue.put(("stop", ""))
                                elif self._state == "idle":
                                    self._press_time = time.monotonic()
                                    if mode == "toggle":
                                        self._state = "toggled"
                                        self._event_queue.put(("start", "toggled"))
                                    elif mode == "hold":
                                        self._state = "holding"
                                        self._event_queue.put(("start", "holding"))
                                    else:
                                        self._state = "pending_hybrid"
                                        self._event_queue.put(("start", "hybrid"))
                            return 1
                    elif is_up:
                        if self._swallowing_x:
                            self._swallowing_x = False
                            self._handle_chord_release()
                            return 1
                elif vk == VK_ESCAPE and is_down:
                    with self._lock:
                        if self._state in ("pending_hybrid", "holding", "toggled"):
                            self._state = "idle"
                            self._event_queue.put(("cancel", ""))
                            return 1

        return user32.CallNextHookEx(self._hook_handle, nCode, wParam, lParam)

    def _hook_loop(self) -> None:
        self._hook_thread_id = kernel32.GetCurrentThreadId()
        self._hook_proc_ref = HOOKPROC(self._low_level_proc)
        self._hook_handle = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._hook_proc_ref, None, 0
        )
        if not self._hook_handle:
            err = ctypes.get_last_error()
            log.error("Failed to install WH_KEYBOARD_LL hook (WinError=%s)", err)
            return
        log.info("Installed WH_KEYBOARD_LL hook for Alt+X (handle=0x%x)", self._hook_handle)

        msg = wintypes.MSG()
        try:
            while self._running:
                res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if res <= 0:
                    break
        finally:
            if self._hook_handle:
                user32.UnhookWindowsHookEx(self._hook_handle)
                self._hook_handle = None
