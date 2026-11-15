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
from winflow.settings_ui import SettingsWindowController

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
    res = eng.process_audio(samples, wav_bytes, cm.data)
    eng.close()
    print(f"  Groq Cloud result ({res.latency_ms} ms): {res.final_text!r}")
    assert res.final_text and res.latency_ms < 2500

    assert set_clipboard_text("Alt+X Clipboard Check")
    assert get_clipboard_text() == "Alt+X Clipboard Check"

    ui = SettingsWindowController(cm, on_config_saved=lambda: None)
    ui.start()
    ui.open_settings(0)
    time.sleep(0.25)
    assert ui._win is not None and ui._win.winfo_exists()
    ui.stop()

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
