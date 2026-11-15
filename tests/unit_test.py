"""Deterministic CI unit tests (runs cleanly on GitHub Actions windows-latest without audio hardware or API keys)."""

from __future__ import annotations

import ctypes
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from winflow.cleaner import (
    build_llm_cleanup_system_prompt,
    clean_text_locally,
    contains_devanagari,
    is_hallucination,
    transliterate_devanagari_to_hinglish,
)
from winflow.config import SUPPORTED_PROVIDERS, ConfigManager
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
from winflow.settings_ui import SettingsWindowController, _SettingsJsApi
from winflow.sounds import _build_cue_wav, play_sound

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

    # Verify Hinglish ("Apna") Romanization & Devanagari elimination
    dev_input = "अरे um भाई अपना काम जल्दी कर लो new line मुझे समझ नहीं आया।"
    hinglish_out = clean_text_locally(
        dev_input,
        remove_fillers=True,
        smart_formatting=True,
        style_mode="clean",
        custom_dictionary="GitHub",
        language="hinglish",
    )
    assert not contains_devanagari(hinglish_out), f"Devanagari leaked: {hinglish_out}"
    assert "apna" in hinglish_out.lower(), f"Expected 'apna' in {hinglish_out!r}"
    assert "bhai" in hinglish_out.lower(), f"Expected 'bhai' in {hinglish_out!r}"
    assert "kaam" in hinglish_out.lower(), f"Expected 'kaam' in {hinglish_out!r}"
    assert "samajh" in hinglish_out.lower(), f"Expected 'samajh' in {hinglish_out!r}"
    assert "\n" in hinglish_out
    assert transliterate_devanagari_to_hinglish("अपना") == "apna"

    hinglish_prompt = build_llm_cleanup_system_prompt(
        True, True, "clean", "WinFlow", language="hinglish"
    )
    assert "HINGLISH" in hinglish_prompt and "Apna" in hinglish_prompt


def test_config_and_settings_ipc() -> None:
    for name in ("start", "stop", "cancel", "apply", "click"):
        wav = _build_cue_wav(name)
        assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE", f"Bad WAV for {name}"
    play_sound("click", enabled=False)

    with tempfile.TemporaryDirectory() as td:
        cfg_file = Path(td) / "cfg.json"
        cm = ConfigManager(cfg_file)
        assert cm.data.ui_sounds is True

        reloaded = []
        ctrl = SettingsWindowController(cm, on_config_saved=lambda: reloaded.append(True))
        ctrl.start()
        try:
            api = _SettingsJsApi(ConfigManager(cfg_file), initial_tab=0)
            st = api.get_state()
            assert st["ui_sounds"] is True
            for p in ("groq", "gemini", "openai", "anthropic", "xai", "nvidia", "kimi", "custom"):
                assert p in SUPPORTED_PROVIDERS

            st["style_mode"] = "polished"
            st["ui_sounds"] = False
            st["language"] = "hinglish"
            st["cloud_provider"] = "xai"
            st["api_keys"] = {
                "xai": "xai-test-key",
                "nvidia": "nvapi-test-key",
                "anthropic": "sk-ant-test-key",
                "openai": "sk-openai-test-key",
                "kimi": "sk-kimi-test-key",
            }
            assert api.save_state(st) is True

            for _ in range(15):
                if reloaded:
                    break
                time.sleep(0.05)

            assert len(reloaded) >= 1, "SettingsWindowController did not receive reload event!"
            assert cm.data.style_mode == "polished"
            assert cm.data.ui_sounds is False
            assert cm.data.language == "hinglish"
            assert cm.data.cloud_provider == "xai"
            assert cm.data.get_api_key("xai") == "xai-test-key"
            assert cm.data.get_api_key("nvidia") == "nvapi-test-key"
            assert cm.data.get_api_key("anthropic") == "sk-ant-test-key"
            assert cm.data.get_api_key("openai") == "sk-openai-test-key"
            assert cm.data.get_api_key("kimi") == "sk-kimi-test-key"
        finally:
            ctrl.stop()

        cm.add_history("um hi", "Hi.", "Local", 210)
        cm2 = ConfigManager(cfg_file)
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
    test_config_and_settings_ipc()
    test_alt_x_hook_and_win_h_passthrough()
    print("All CI unit tests passed!")
