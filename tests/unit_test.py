"""Deterministic CI unit tests (runs cleanly on GitHub Actions windows-latest without audio hardware or API keys)."""

from __future__ import annotations

import ctypes
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("WINFLOW_INSTANCE_ID", f"_unit_{os.getpid()}")

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
    VK_LMENU,
    VK_LWIN,
    VK_X,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
)
from winflow.overlay import (
    FloatingPillOverlay,
    HWND_TOPMOST,
    SWP_NOACTIVATE,
    SWP_SHOWWINDOW,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    WS_EX_TOPMOST,
    WS_POPUP,
)
from winflow.settings_ui import MINIMALIST_HTML, SettingsWindowController, _SettingsJsApi
from winflow.sounds import _build_cue_wav, play_sound
from winflow.updater import (
    UpdateManager,
    extract_release_update_info,
    is_newer_version,
    parse_version,
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


def test_minimal_ui_has_no_overexplanation_or_examples() -> None:
    assert "e.g." not in MINIMALIST_HTML
    assert "Zero PC load" not in MINIMALIST_HTML
    assert "hallucinations" not in MINIMALIST_HTML
    assert "id=\"btnApplyKey\"" in MINIMALIST_HTML
    assert "id=\"btnSaveAll\"" in MINIMALIST_HTML


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
        assert cm.data.custom_dictionary == ""

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

            cm.add_history("um hi", "Hi.", "Offline", 210)
            assert len(ConfigManager(cfg_file).data.history) == 1
            assert api.clear_history() is True
            assert len(ConfigManager(cfg_file).data.history) == 0
        finally:
            ctrl.stop()


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


def test_overlay_topmost_z_order_and_self_healing() -> None:
    import ctypes.wintypes as wt
    from winflow.overlay import user32

    pill = FloatingPillOverlay(lambda: 0.7)
    pill.start()
    orig_hwnd = pill._hwnd
    assert orig_hwnd
    competing = None
    try:
        pill.show_toast("Ready", duration_s=0.18)
        time.sleep(0.08)
        assert user32.IsWindowVisible(pill._hwnd)
        time.sleep(0.22)
        assert not user32.IsWindowVisible(pill._hwnd)

        competing = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            "STATIC",
            "CompetingTop",
            WS_POPUP,
            400,
            600,
            300,
            80,
            None,
            None,
            None,
            None,
        )
        user32.SetWindowPos(competing, HWND_TOPMOST, 400, 600, 300, 80, SWP_NOACTIVATE | SWP_SHOWWINDOW)

        pill.show_listening("Listening… (Alt+X)")
        time.sleep(0.14)
        assert user32.IsWindowVisible(pill._hwnd)

        user32.GetTopWindow.argtypes = [wt.HWND]
        user32.GetTopWindow.restype = wt.HWND
        user32.GetWindow.argtypes = [wt.HWND, wt.UINT]
        user32.GetWindow.restype = wt.HWND
        cur = user32.GetTopWindow(None)
        order: list[str] = []
        while cur:
            if cur == pill._hwnd:
                order.append("PILL")
            elif cur == competing:
                order.append("COMPETING")
            cur = user32.GetWindow(cur, 2)
        assert order == ["PILL", "COMPETING"], f"Pill lost topmost Z-order: {order}"

        # Simulate HWND loss and verify PostThreadMessageW self-recovery
        pill.hide()
        time.sleep(0.08)
        user32.PostMessageW(orig_hwnd, 0x0010, 0, 0)  # WM_CLOSE
        time.sleep(0.08)
        assert not user32.IsWindow(orig_hwnd)
        pill.show_listening("Listening… (Alt+X)")
        time.sleep(0.16)
        assert pill._hwnd and pill._hwnd != orig_hwnd and user32.IsWindowVisible(pill._hwnd)
    finally:
        if competing:
            user32.DestroyWindow(competing)
        pill.stop()


def test_auto_updater_github_check_and_download() -> None:
    import hashlib
    import http.server
    import json
    import socketserver
    import threading

    assert parse_version("v1.4.0") == (1, 4, 0)
    assert is_newer_version("v1.4.1", "1.4.0") is True
    assert is_newer_version("v1.4.0", "1.4.0") is False
    assert is_newer_version("v1.3.1", "1.4.0") is False

    fake_pe = b"MZ" + (b"\x90" * 8190)
    good_sha = hashlib.sha256(fake_pe).hexdigest()

    state_holder = {"sha_in_body": good_sha, "downloads": 0}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            pass

        def do_GET(self) -> None:
            if self.path == "/releases/latest":
                port = self.server.server_address[1]
                payload = {
                    "tag_name": "v9.9.9",
                    "draft": False,
                    "prerelease": False,
                    "html_url": "https://github.com/piyushmaurya6/winflow/releases/tag/v9.9.9",
                    "body": f"- `WinFlow-Setup.exe`: `{state_holder['sha_in_body']}`",
                    "assets": [
                        {
                            "name": "WinFlow-Setup.exe",
                            "size": len(fake_pe),
                            "browser_download_url": f"http://127.0.0.1:{port}/WinFlow-Setup.exe",
                        }
                    ],
                }
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            elif self.path == "/WinFlow-Setup.exe":
                state_holder["downloads"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(fake_pe)))
                self.end_headers()
                self.wfile.write(fake_pe)
            else:
                self.send_response(404)
                self.end_headers()

    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as httpd:
        port = httpd.server_address[1]
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                up_dir = Path(td) / "updates"
                ready_hits: list[str] = []

                # 1. Bad SHA-256 must be rejected
                state_holder["sha_in_body"] = "0" * 64
                mgr_bad = UpdateManager(
                    current_version="1.4.0",
                    api_url=f"http://127.0.0.1:{port}/releases/latest",
                    updates_dir=up_dir,
                    on_update_ready=lambda info, m: ready_hits.append(info.tag_name),
                    min_auto_interval_s=0.0,
                )
                assert mgr_bad.check_and_download_sync() is None
                assert mgr_bad.status == "error"
                assert ready_hits == []

                # 2. Valid SHA-256 downloads, verifies, and fires callback
                state_holder["sha_in_body"] = good_sha
                state_holder["downloads"] = 0
                mgr = UpdateManager(
                    current_version="1.4.0",
                    api_url=f"http://127.0.0.1:{port}/releases/latest",
                    updates_dir=up_dir,
                    on_update_ready=lambda info, m: ready_hits.append(info.tag_name),
                    min_auto_interval_s=0.0,
                )
                res = mgr.check_and_download_sync()
                assert res is not None and res.tag_name == "v9.9.9"
                assert res.installer_path and res.installer_path.exists()
                assert state_holder["downloads"] == 1
                assert ready_hits == ["v9.9.9"]

                # 3. Second check reuses verified cache without re-downloading
                res2 = mgr.check_and_download_sync()
                assert res2 is not None and state_holder["downloads"] == 1
        finally:
            httpd.shutdown()

    assert extract_release_update_info({"tag_name": "v9.9.9", "draft": True}, "1.4.0") is None


def test_bluetooth_hotplug_and_mic_resampling() -> None:
    from unittest.mock import patch
    import numpy as np
    import winflow.audio as wa

    # 1. Verify 48kHz, 44.1kHz, and 8kHz Bluetooth CVSD -> 16kHz resampling
    for src_sr in (48000, 44100, 8000):
        one_sec = np.sin(np.linspace(0, 20, src_sr, dtype=np.float32))
        out_16k = wa._resample_to_16k(one_sec, src_sr)
        assert abs(len(out_16k) - 16000) <= 1
        assert out_16k.dtype == np.float32

    # 2. Simulate Desktop PC booting with 0 mics, then Bluetooth earbuds finishing refresh 0.3s after Alt+X!
    fp_seq = [
        (0, -1, ()),
        (0, -1, ()),
        (1, 0, ("Headset (Airdopes 800)",)),
        (1, 0, ("Headset (Airdopes 800)",)),
    ]
    call_idx = [0]

    def _fake_fp():
        i = min(call_idx[0], len(fp_seq) - 1)
        call_idx[0] += 1
        return fp_seq[i]

    class _DummyStream:
        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    waiting_msgs: list[str] = []
    reinit_calls: list[bool] = []
    rec = wa.AudioRecorder()

    orig_last = wa._LAST_PA_FINGERPRINT
    try:
        wa._LAST_PA_FINGERPRINT = (0, -1, ())
        with (
            patch.object(wa, "_get_live_wavein_fingerprint", side_effect=_fake_fp),
            patch.object(wa.sd, "_terminate", side_effect=lambda: reinit_calls.append(True)),
            patch.object(wa.sd, "_initialize", side_effect=lambda: None),
            patch.object(
                wa,
                "_usable_input_devices",
                return_value=[(2, "Headset (Airdopes 800)", "MME", 16000.0, 1)],
            ),
            patch.object(rec, "_try_open_candidate", return_value=(_DummyStream(), 16000)),
        ):
            rec.start(on_waiting=waiting_msgs.append, wait_for_bt_s=1.5)
            assert waiting_msgs == ["Connecting mic…"]
            assert len(reinit_calls) >= 1, "PortAudio was not refreshed after Bluetooth hotplug!"
            rec.cancel()
    finally:
        wa._LAST_PA_FINGERPRINT = orig_last


if __name__ == "__main__":
    test_win32_struct_sizes()
    test_minimal_ui_has_no_overexplanation_or_examples()
    test_cleaner_disfluencies_and_commands()
    test_config_and_settings_ipc()
    test_alt_x_hook_and_win_h_passthrough()
    test_overlay_topmost_z_order_and_self_healing()
    test_auto_updater_github_check_and_download()
    test_bluetooth_hotplug_and_mic_resampling()
    print("All CI unit tests passed!")
