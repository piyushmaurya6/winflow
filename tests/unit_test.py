"""Deterministic CI unit tests (runs cleanly on GitHub Actions windows-latest without audio hardware or API keys)."""

from __future__ import annotations

import ctypes
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from winflow.cleaner import build_llm_cleanup_system_prompt, clean_text_locally, is_hallucination
from winflow.config import ConfigManager
from winflow.hotkey import (
    AltXHotkeyListener,
    INPUT,
    KBDLLHOOKSTRUCT,
    LLKHF_ALTDOWN,
    VK_ESCAPE,
    VK_LMENU,
    VK_LWIN,
    VK_X,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
)

VK_H = 0x48


def _feed(hk: AltXHotkeyListener, vk: int, is_up: bool = False, alt_flag: bool = False) -> int:
    flags = (0x80 if is_up else 0) | (LLKHF_ALTDOWN if alt_flag else 0)
    kb = KBDLLHOOKSTRUCT(vkCode=vk, scanCode=0, flags=flags, time=100, dwExtraInfo=0)
    msg = (WM_SYSKEYUP if is_up else WM_SYSKEYDOWN) if alt_flag else (WM_KEYUP if is_up else WM_KEYDOWN)
    return hk._low_level_proc(0, msg, ctypes.addressof(kb))


def test_win32_struct_sizes() -> None:
    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(INPUT) == expected, f"Expected {expected}, got {ctypes.sizeof(INPUT)}"


def test_cleaner_disfluencies_and_commands() -> None:
    raw = "um, can Can we somehow create, you know, i i think wisperflo new line let me let me know."
    out = clean_text_locally(
        raw,
        remove_fillers=True,
        smart_formatting=True,
        style_mode="clean",
        custom_dictionary="Wispr Flow, WinFlow, GitHub, Windows",
    )
    assert "Wispr Flow" in out
    assert "\n" in out
    assert "can can" not in out.lower()
    assert "let me let me" not in out.lower()
    assert "um" not in out.lower()
    assert is_hallucination("Thank you for watching.")
    assert not is_hallucination("Ship the pull request.")
    prompt = build_llm_cleanup_system_prompt(True, True, "clean", "Wispr Flow")
    assert "Wispr Flow" in prompt


def test_config_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as td:
        cm = ConfigManager(Path(td) / "cfg.json")
        cm.update(engine_mode="local", style_mode="polished")
        cm.add_history("um hi", "Hi.", "Local", 210)
        cm2 = ConfigManager(Path(td) / "cfg.json")
        assert cm2.data.style_mode == "polished"
        assert len(cm2.data.history) == 1


def test_alt_x_hook_and_win_h_passthrough() -> None:
    events: list[str] = []
    hk = AltXHotkeyListener(
        on_start=lambda m: events.append(f"start:{m}"),
        on_stop=lambda: events.append("stop"),
        on_cancel=lambda: events.append("cancel"),
        on_mode_changed=lambda m: events.append(f"mode:{m}"),
        get_hold_threshold_ms=lambda: 220,
    )
    hk.start()
    time.sleep(0.15)
    try:
        # Native Win+H passes through untouched
        assert _feed(hk, VK_LWIN, False) == 0
        assert _feed(hk, VK_H, False) == 0
        assert _feed(hk, VK_H, True) == 0
        assert _feed(hk, VK_LWIN, True) == 0

        # Alt+X tap toggles on, second tap stops
        assert _feed(hk, VK_LMENU, False, True) == 0
        assert _feed(hk, VK_X, False, True) == 1
        time.sleep(0.04)
        assert _feed(hk, VK_X, True, True) == 1
        assert _feed(hk, VK_LMENU, True, False) == 0
        time.sleep(0.06)
        assert events == ["start:hybrid", "mode:toggled"]

        events.clear()
        assert _feed(hk, VK_LMENU, False, True) == 0
        assert _feed(hk, VK_X, False, True) == 1
        assert _feed(hk, VK_X, True, True) == 1
        assert _feed(hk, VK_LMENU, True, False) == 0
        time.sleep(0.06)
        assert events == ["stop"]
    finally:
        hk.stop()


if __name__ == "__main__":
    test_win32_struct_sizes()
    test_cleaner_disfluencies_and_commands()
    test_config_roundtrip()
    test_alt_x_hook_and_win_h_passthrough()
    print("All CI unit tests passed!")
