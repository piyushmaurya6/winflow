"""Comprehensive automated self-test suite for WinFlow (Alt+X + Native Win+H untouched)."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from winflow.config import ConfigManager
from winflow.engine import SpeechEngine
from winflow.hotkey import (
    AltXHotkeyListener,
    KBDLLHOOKSTRUCT,
    LLKHF_ALTDOWN,
    VK_ESCAPE,
    VK_LCONTROL,
    VK_LMENU,
    VK_LWIN,
    VK_X,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
)
from winflow.injector import get_clipboard_text, set_clipboard_text
from winflow.overlay import FloatingPillOverlay
from winflow.settings_ui import MINIMALIST_HTML, SettingsWindowController, _SettingsJsApi
from winflow.sounds import play_sound

VK_H = 0x48


def feed_hook(
    hk: AltXHotkeyListener,
    vk: int,
    is_up: bool = False,
    alt_flag: bool = False,
) -> int:
    flags = (0x80 if is_up else 0) | (LLKHF_ALTDOWN if alt_flag else 0)
    kb = KBDLLHOOKSTRUCT(
        vkCode=vk,
        scanCode=0,
        flags=flags,
        time=int(time.monotonic() * 1000) & 0xFFFFFFFF,
        dwExtraInfo=0,
    )
    if alt_flag:
        msg = WM_SYSKEYUP if is_up else WM_SYSKEYDOWN
    else:
        msg = WM_KEYUP if is_up else WM_KEYDOWN
    return hk._low_level_proc(0, msg, ctypes.addressof(kb))


def synthesize_speech_wav(text: str, out_path: Path) -> tuple[np.ndarray, bytes]:
    ps_script = f"""
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth.SetOutputToWaveFile("{out_path}", $fmt)
$synth.Speak("{text}")
$synth.Dispose()
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps_script], check=True)
    wav_bytes = out_path.read_bytes()
    with wave.open(str(out_path), "rb") as wf:
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        samples = pcm.astype(np.float32) / 32768.0
    return samples, wav_bytes


def test_1_alt_x_and_native_win_h() -> None:
    print("[Test 1] Verifying Win+H stays untouched & Alt+X triggers WinFlow...")
    events: list[str] = []
    hk = AltXHotkeyListener(
        on_start=lambda mode: events.append(f"start:{mode}"),
        on_stop=lambda: events.append("stop"),
        on_cancel=lambda: events.append("cancel"),
        on_mode_changed=lambda m: events.append(f"mode:{m}"),
        get_hotkey_mode=lambda: "hybrid",
        get_hold_threshold_ms=lambda: 250,
    )
    hk.start()
    time.sleep(0.2)
    assert hk._hook_handle, "SetWindowsHookExW failed!"

    try:
        # 1A. Win + H MUST pass through 100% untouched (return 0) for Native Windows Voice Typing!
        assert feed_hook(hk, VK_LWIN, is_up=False) == 0
        assert feed_hook(hk, VK_H, is_up=False) == 0, "Win+H was blocked — must stay native!"
        assert feed_hook(hk, VK_H, is_up=True) == 0
        assert feed_hook(hk, VK_LWIN, is_up=True) == 0
        assert events == []

        # 1B. Plain 'x' and 'Ctrl+Alt+X' must pass through untouched (return 0)
        assert feed_hook(hk, VK_X, is_up=False) == 0
        assert feed_hook(hk, VK_X, is_up=True) == 0
        feed_hook(hk, VK_LCONTROL, is_up=False)
        feed_hook(hk, VK_LMENU, is_up=False, alt_flag=True)
        assert feed_hook(hk, VK_X, is_up=False, alt_flag=True) == 0, "Ctrl+Alt+X should pass through!"
        feed_hook(hk, VK_X, is_up=True, alt_flag=True)
        feed_hook(hk, VK_LMENU, is_up=True)
        feed_hook(hk, VK_LCONTROL, is_up=True)
        assert events == []

        # 1C. Quick Tap Alt + X (<250ms) -> swallowed (return 1) & latches toggle mode
        assert feed_hook(hk, VK_LMENU, is_up=False, alt_flag=True) == 0
        assert feed_hook(hk, VK_X, is_up=False, alt_flag=True) == 1, "Alt+X keydown NOT swallowed!"
        time.sleep(0.05)
        assert feed_hook(hk, VK_X, is_up=True, alt_flag=True) == 1, "Alt+X keyup NOT swallowed!"
        assert feed_hook(hk, VK_LMENU, is_up=True) == 0
        time.sleep(0.08)
        print("  1C Quick tap #1 Alt+X events:", events)
        assert events == ["start:hybrid", "mode:toggled"], f"Unexpected: {events}"

        # 1D. Second Tap Alt + X (releasing Alt before X) -> stops cleanly, plain X works after
        events.clear()
        assert feed_hook(hk, VK_LMENU, is_up=False, alt_flag=True) == 0
        assert feed_hook(hk, VK_X, is_up=False, alt_flag=True) == 1
        time.sleep(0.04)
        assert feed_hook(hk, VK_LMENU, is_up=True) == 0
        assert feed_hook(hk, VK_X, is_up=True, alt_flag=False) == 1
        time.sleep(0.08)
        print("  1D Quick tap #2 Alt+X events:", events)
        assert events == ["stop"], f"Expected ['stop'], got {events}"
        assert feed_hook(hk, VK_X, is_up=False) == 0
        assert feed_hook(hk, VK_X, is_up=True) == 0

        # 1E. Hold Alt + X (>250ms) -> Push-to-Talk!
        events.clear()
        feed_hook(hk, VK_LMENU, is_up=False, alt_flag=True)
        assert feed_hook(hk, VK_X, is_up=False, alt_flag=True) == 1
        time.sleep(0.15)
        assert feed_hook(hk, VK_X, is_up=False, alt_flag=True) == 1
        time.sleep(0.15)
        assert feed_hook(hk, VK_X, is_up=False, alt_flag=True) == 1
        assert feed_hook(hk, VK_X, is_up=True, alt_flag=True) == 1
        feed_hook(hk, VK_LMENU, is_up=True)
        time.sleep(0.08)
        print("  1E Hold Alt+X events:", events)
        assert events == ["start:hybrid", "mode:holding", "stop"], f"Unexpected: {events}"

        # 1F. Escape cancels active Alt+X recording
        events.clear()
        feed_hook(hk, VK_LMENU, is_up=False, alt_flag=True)
        assert feed_hook(hk, VK_X, is_up=False, alt_flag=True) == 1
        feed_hook(hk, VK_X, is_up=True, alt_flag=True)
        feed_hook(hk, VK_LMENU, is_up=True)
        time.sleep(0.06)
        assert feed_hook(hk, VK_ESCAPE, is_up=False) == 1
        feed_hook(hk, VK_ESCAPE, is_up=True)
        time.sleep(0.06)
        assert "cancel" in events
    finally:
        hk.stop()
    print("  [PASS] Test 1 passed 100%!")


def test_2_groq_cloud_and_ui_and_vbs() -> None:
    print("[Test 2] Testing Groq Cloud speed + Settings UI + Start-WinFlow.vbs longevity...")
    cm = ConfigManager()
    assert cm.data.engine_mode == "cloud" and cm.data.groq_api_key.strip()

    with tempfile.TemporaryDirectory() as td:
        samples, wav_bytes = synthesize_speech_wav(
            "Um, testing Alt plus X with Wispr Flow cleanup on Windows.",
            Path(td) / "short.wav",
        )
    eng = SpeechEngine()
    # Test Groq Cloud (~800ms)
    from dataclasses import replace
    groq_cfg = replace(cm.data, engine_mode="cloud", cloud_provider="groq")
    res = eng.process_audio(samples, wav_bytes, groq_cfg)
    print(f"  Groq Cloud result ({res.latency_ms} ms): {res.final_text!r}")
    assert res.final_text and res.latency_ms < 2500

    # Also test Gemini Cloud if user has a Gemini key configured!
    if cm.data.gemini_api_key.strip():
        gem_cfg = replace(cm.data, engine_mode="cloud", cloud_provider="gemini")
        res_gem = eng.process_audio(samples, wav_bytes, gem_cfg)
        print(f"  Gemini Cloud result ({res_gem.latency_ms} ms): {res_gem.final_text!r}")
        assert res_gem.final_text and res_gem.latency_ms < 5000
    eng.close()

    assert set_clipboard_text("Alt+X Clipboard Check")
    assert get_clipboard_text() == "Alt+X Clipboard Check"

    # Verify tactile sound synthesis & WinMM async playback never blocks
    t_snd = time.perf_counter()
    play_sound("click", enabled=True)
    play_sound("apply", enabled=True)
    assert (time.perf_counter() - t_snd) < 0.15

    # Verify isolated WebView2 Settings UI, Paste, inline Apply Key, sticky Apply & Save, and IPC reload
    print("  Testing WebView2 minimalist UI (layout bounds, clipboard paste, Apply Key, sound)...")
    import webview

    with tempfile.TemporaryDirectory() as td:
        tmp_cfg = Path(td) / "ui_test_config.json"
        cm_daemon = ConfigManager(tmp_cfg)
        reload_hits: list[str] = []
        ui_ctrl = SettingsWindowController(
            cm_daemon,
            on_config_saved=lambda: reload_hits.append(cm_daemon.data.engine_mode),
        )
        ui_ctrl.start()

        api = _SettingsJsApi(ConfigManager(tmp_cfg), initial_tab=0)
        ui_results: dict = {}

        win = webview.create_window(
            "WinFlow UI SelfTest",
            html=MINIMALIST_HTML,
            js_api=api,
            width=580,
            height=640,
            background_color="#0A0B0E",
        )

        def _drive_webview() -> None:
            try:
                time.sleep(0.8)
                # 1. Verify both Apply buttons exist and are 100% inside the visible viewport (never clipped!)
                bounds = win.evaluate_js(
                    """(() => {
                        const b1 = document.getElementById('btnApplyKey').getBoundingClientRect();
                        const b2 = document.getElementById('btnSaveAll').getBoundingClientRect();
                        return {
                            vh: window.innerHeight,
                            vw: window.innerWidth,
                            applyKeyVisible: b1.width > 60 && b1.height > 28 && b1.bottom <= window.innerHeight,
                            saveAllVisible: b2.width > 80 && b2.height > 28 && b2.bottom <= window.innerHeight
                        };
                    })()"""
                )
                ui_results["bounds"] = bounds

                # 2. Put real Groq key on clipboard, paste via Paste button, and verify zero freeze!
                set_clipboard_text(cm.data.groq_api_key.strip())
                win.evaluate_js("pasteFromClipboard()")
                time.sleep(0.25)
                pasted_len = win.evaluate_js("document.getElementById('apiKeyInput').value.length")
                ui_results["pasted_len"] = pasted_len

                # 3. Click inline Apply Key -> verifies key over HTTP & signals daemon!
                win.evaluate_js("applyApiKey()")
                for _ in range(30):
                    status_cls = win.evaluate_js("document.getElementById('keyFeedback').className")
                    if "ok" in (status_cls or ""):
                        break
                    time.sleep(0.15)
                ui_results["feedback_cls"] = win.evaluate_js("document.getElementById('keyFeedback').className")
                ui_results["feedback_msg"] = win.evaluate_js("document.getElementById('keyStatusMsg').textContent")

                # 4. Switch tabs & test tactile sound synthesis
                win.evaluate_js("switchTab(1); uiTone('press'); switchTab(2);")
                time.sleep(0.2)
                ui_results["active_pane"] = win.evaluate_js("document.getElementById('pane-2').classList.contains('active')")
            finally:
                win.destroy()

        webview.start(_drive_webview, gui="edgechromium")
        time.sleep(0.2)
        ui_ctrl.stop()

        print(f"  WebView2 UI check: {ui_results}, reload_hits={reload_hits}")
        assert ui_results["bounds"]["applyKeyVisible"] is True, f"Apply Key clipped: {ui_results}"
        assert ui_results["bounds"]["saveAllVisible"] is True, f"Apply & Save clipped: {ui_results}"
        assert ui_results["pasted_len"] == len(cm.data.groq_api_key.strip())
        assert "ok" in ui_results["feedback_cls"], f"Apply Key failed: {ui_results}"
        assert ui_results["active_pane"] is True
        assert "cloud" in reload_hits, "Daemon did not receive live reload from Apply Key!"

    pill = FloatingPillOverlay(get_audio_level=lambda: 0.6)
    pill.start()
    pill.show_listening("Listening… (Alt+X)")
    time.sleep(0.15)
    pill.show_toast("Pasted", duration_s=0.15)
    time.sleep(0.28)
    assert pill._state == "hidden"
    pill.stop()

    log_file = PROJECT_ROOT / "winflow.log"
    if log_file.exists():
        log_file.unlink()

    vbs_path = PROJECT_ROOT / "Start-WinFlow.vbs"
    subprocess.Popen(["wscript.exe", str(vbs_path)], cwd=str(PROJECT_ROOT))
    time.sleep(4.5)

    ps_check = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*run_winflow.py*' } | Select-Object -ExpandProperty ProcessId",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    pids = [int(line.strip()) for line in ps_check.stdout.splitlines() if line.strip().isdigit()]
    print(f"  Start-WinFlow.vbs alive pythonw PIDs: {pids}")
    assert len(pids) > 0

    log_txt = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    print(f"  winflow.log: {log_txt.strip()}")
    assert "Installed WH_KEYBOARD_LL hook for Alt+X" in log_txt
    assert "[ERROR]" not in log_txt

    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    print("  [PASS] Test 2 passed 100%!")


if __name__ == "__main__":
    test_1_alt_x_and_native_win_h()
    test_2_groq_cloud_and_ui_and_vbs()
    print("\n=== ALL ALT+X + NATIVE WIN+H TESTS PASSED 100%! ===")
