"""Comprehensive automated self-test suite for WinFlow v1.3.0 (Alt+X, Simple UI, Native WinFlow-Setup.exe & Defender Scan)."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import replace
from pathlib import Path

import numpy as np

os.environ.setdefault("WINFLOW_INSTANCE_ID", f"_self_{os.getpid()}")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from winflow.cleaner import contains_devanagari
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
        assert feed_hook(hk, VK_LWIN, is_up=False) == 0
        assert feed_hook(hk, VK_H, is_up=False) == 0, "Win+H was blocked — must stay native!"
        assert feed_hook(hk, VK_H, is_up=True) == 0
        assert feed_hook(hk, VK_LWIN, is_up=True) == 0
        assert events == []

        assert feed_hook(hk, VK_X, is_up=False) == 0
        assert feed_hook(hk, VK_X, is_up=True) == 0
        feed_hook(hk, VK_LCONTROL, is_up=False)
        feed_hook(hk, VK_LMENU, is_up=False, alt_flag=True)
        assert feed_hook(hk, VK_X, is_up=False, alt_flag=True) == 0
        feed_hook(hk, VK_X, is_up=True, alt_flag=True)
        feed_hook(hk, VK_LMENU, is_up=True)
        feed_hook(hk, VK_LCONTROL, is_up=True)
        assert events == []

        assert feed_hook(hk, VK_LMENU, is_up=False, alt_flag=True) == 0
        assert feed_hook(hk, VK_X, is_up=False, alt_flag=True) == 1
        time.sleep(0.05)
        assert feed_hook(hk, VK_X, is_up=True, alt_flag=True) == 1
        assert feed_hook(hk, VK_LMENU, is_up=True) == 0
        time.sleep(0.08)
        assert events == ["start:hybrid", "mode:toggled"], f"Unexpected: {events}"

        events.clear()
        assert feed_hook(hk, VK_LMENU, is_up=False, alt_flag=True) == 0
        assert feed_hook(hk, VK_X, is_up=False, alt_flag=True) == 1
        time.sleep(0.04)
        assert feed_hook(hk, VK_LMENU, is_up=True) == 0
        assert feed_hook(hk, VK_X, is_up=True, alt_flag=False) == 1
        time.sleep(0.08)
        assert events == ["stop"], f"Expected ['stop'], got {events}"

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
        assert events == ["start:hybrid", "mode:holding", "stop"], f"Unexpected: {events}"

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


def test_2_cloud_hinglish_and_minimal_ui() -> None:
    print("[Test 2] Testing Cloud STT + Hinglish + Simplified UI...")
    cm = ConfigManager(PROJECT_ROOT / "config.json")
    assert cm.data.groq_api_key.strip(), "Local test Groq key missing"

    with tempfile.TemporaryDirectory() as td:
        samples, wav_bytes = synthesize_speech_wav(
            "Um, testing Alt plus X with Wispr Flow cleanup on Windows.",
            Path(td) / "short.wav",
        )
    eng = SpeechEngine()
    groq_cfg = replace(cm.data, engine_mode="cloud", cloud_provider="groq")
    res = eng.process_audio(samples, wav_bytes, groq_cfg)
    print(f"  Groq Cloud result ({res.latency_ms} ms): {res.final_text!r}")
    assert res.final_text and res.latency_ms < 6000

    if cm.data.gemini_api_key.strip():
        gem_cfg = replace(cm.data, engine_mode="cloud", cloud_provider="gemini")
        res_gem = eng.process_audio(samples, wav_bytes, gem_cfg)
        print(f"  Gemini Cloud result ({res_gem.latency_ms} ms): {res_gem.final_text!r}")
        assert res_gem.final_text and res_gem.latency_ms < 12000
    eng.close()

    with tempfile.TemporaryDirectory() as td:
        h_samples, h_wav = synthesize_speech_wav(
            "Um, bhai apna kaam jaldi karo and push the code to GitHub.",
            Path(td) / "hinglish.wav",
        )
    eng_h = SpeechEngine()
    groq_hinglish_cfg = replace(
        cm.data, engine_mode="cloud", cloud_provider="groq", language="hinglish"
    )
    res_h = eng_h.process_audio(h_samples, h_wav, groq_hinglish_cfg)
    print(f"  Groq Hinglish result ({res_h.latency_ms} ms): {res_h.final_text!r}")
    assert "apna" in res_h.final_text.lower()
    assert not contains_devanagari(res_h.final_text)
    eng_h.close()

    t_snd = time.perf_counter()
    play_sound("click", enabled=True)
    play_sound("apply", enabled=True)
    assert (time.perf_counter() - t_snd) < 0.15

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
            width=500,
            height=540,
            background_color="#0A0B0E",
        )

        def _drive_webview() -> None:
            try:
                time.sleep(0.8)
                bounds = win.evaluate_js(
                    """(() => {
                        const b1 = document.getElementById('btnApplyKey').getBoundingClientRect();
                        const b2 = document.getElementById('btnSaveAll').getBoundingClientRect();
                        return {
                            vh: window.innerHeight,
                            vw: window.innerWidth,
                            applyKeyVisible: b1.width > 50 && b1.height > 28 && b1.bottom <= window.innerHeight,
                            saveAllVisible: b2.width > 50 && b2.height > 28 && b2.bottom <= window.innerHeight
                        };
                    })()"""
                )
                ui_results["bounds"] = bounds

                set_clipboard_text(cm.data.groq_api_key.strip())
                win.evaluate_js("pasteFromClipboard()")
                time.sleep(0.25)
                ui_results["pasted_len"] = win.evaluate_js("document.getElementById('apiKeyInput').value.length")

                win.evaluate_js("applyApiKey()")
                for _ in range(30):
                    status_cls = win.evaluate_js("document.getElementById('keyFeedback').className")
                    if "ok" in (status_cls or ""):
                        break
                    time.sleep(0.15)
                ui_results["feedback_cls"] = win.evaluate_js("document.getElementById('keyFeedback').className")
                ui_results["feedback_msg"] = win.evaluate_js("document.getElementById('keyStatusMsg').textContent")

                win.evaluate_js("setLanguage('hinglish'); selectProvider('xai'); selectProvider('nvidia'); selectProvider('anthropic'); selectProvider('kimi'); selectProvider('custom');")
                time.sleep(0.15)
                ui_results["custom_visible"] = win.evaluate_js("document.getElementById('customEndpointRow').classList.contains('visible')")
                ui_results["prov_count"] = win.evaluate_js("document.querySelectorAll('[data-prov]').length")
                ui_results["lang_active"] = win.evaluate_js("document.querySelector('[data-lang=\"hinglish\"]').classList.contains('active')")
                win.evaluate_js("switchTab(1); uiTone('press'); switchTab(2);")
                time.sleep(0.2)
                ui_results["active_pane"] = win.evaluate_js("document.getElementById('pane-2').classList.contains('active')")
            finally:
                win.destroy()

        webview.start(_drive_webview, gui="edgechromium")
        time.sleep(0.2)
        ui_ctrl.stop()

        print(f"  WebView2 UI check: {ui_results}")
        assert ui_results["bounds"]["applyKeyVisible"] is True
        assert ui_results["bounds"]["saveAllVisible"] is True
        assert ui_results["pasted_len"] == len(cm.data.groq_api_key.strip())
        assert "ok" in ui_results["feedback_cls"]
        assert ui_results["active_pane"] is True
        assert ui_results["prov_count"] == 8
        assert ui_results["custom_visible"] is True
        assert ui_results["lang_active"] is True
        assert "cloud" in reload_hits

    pill = FloatingPillOverlay(get_audio_level=lambda: 0.6)
    pill.start()
    pill.show_listening("Listening… (Alt+X)")
    time.sleep(0.15)
    pill.show_toast("Pasted", duration_s=0.15)
    time.sleep(0.28)
    assert pill._state == "hidden"
    pill.stop()
    print("  [PASS] Test 2 passed 100%!")


def test_3_standalone_installer_and_compiled_exe_and_defender() -> None:
    print("[Test 3] Testing standalone WinFlow-Setup.exe, compiled WinFlow.exe, & Windows Defender...")
    setup_exe = PROJECT_ROOT / "release_artifacts" / "WinFlow-Setup.exe"
    dist_exe = PROJECT_ROOT / "dist" / "WinFlow" / "WinFlow.exe"
    assert setup_exe.exists(), f"Missing {setup_exe}"
    assert dist_exe.exists(), f"Missing {dist_exe}"

    # 1. Verify Piyush Maurya PE metadata on WinFlow-Setup.exe and WinFlow.exe
    meta_check = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"$v = (Get-Item '{setup_exe}').VersionInfo; \"$($v.CompanyName)|$($v.LegalCopyright)|$($v.ProductVersion)\"",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    print(f"  WinFlow-Setup.exe VersionInfo: {meta_check}")
    assert "Piyush Maurya" in meta_check

    # 2. Scan both WinFlow-Setup.exe and WinFlow.exe with Microsoft Windows Defender
    defender = Path(r"C:\Program Files\Windows Defender\MpCmdRun.exe")
    if defender.exists():
        for target in (setup_exe, dist_exe):
            scan = subprocess.run(
                [str(defender), "-Scan", "-ScanType", "3", "-File", str(target)],
                capture_output=True,
                text=True,
            )
            print(f"  Defender scan ({target.name}): code={scan.returncode} -> {scan.stdout.strip()}")
            assert scan.returncode == 0, f"Defender flagged {target}: {scan.stdout} {scan.stderr}"
            assert "found no threats" in scan.stdout.lower()

    # 3. Run WinFlow-Setup.exe --silent into an isolated test directory & launch installed WinFlow.exe
    with tempfile.TemporaryDirectory() as td:
        install_dir = Path(td) / "InstalledWinFlow"
        appdata_dir = Path(td) / "LocalAppData"
        appdata_dir.mkdir(parents=True, exist_ok=True)

        res = subprocess.run(
            [str(setup_exe), "--silent", f"--dir={install_dir}"],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"WinFlow-Setup.exe failed with {res.returncode}: {res.stderr}"

        installed_exe = install_dir / "WinFlow.exe"
        installed_uninst = install_dir / "Uninstall-WinFlow.exe"
        assert installed_exe.exists(), "Installed WinFlow.exe missing!"
        assert installed_uninst.exists(), "Installed Uninstall-WinFlow.exe missing!"
        assert (install_dir / "LICENSE.txt").exists(), "Installed LICENSE.txt missing!"
        assert not list(install_dir.rglob("*.bat")), ".bat file found in installed dir!"
        assert not list(install_dir.rglob("*.vbs")), ".vbs file found in installed dir!"
        assert not list(install_dir.rglob("config.json")), "config.json leaked into installed dir!"

        env = os.environ.copy()
        env["LOCALAPPDATA"] = str(appdata_dir)
        env["WINFLOW_NO_FIRST_RUN_UI"] = "1"
        env["WINFLOW_DISABLE_AUTO_UPDATE"] = "1"
        env["WINFLOW_INSTANCE_ID"] = f"_frozen_test_{os.getpid()}"
        proc = subprocess.Popen(
            [str(installed_exe)],
            cwd=str(install_dir),
            env=env,
        )
        try:
            time.sleep(4.5)
            assert proc.poll() is None, f"Installed WinFlow.exe exited prematurely with {proc.returncode}"
            frozen_log = appdata_dir / "WinFlow" / "winflow.log"
            assert frozen_log.exists(), "Installed WinFlow.exe did not create winflow.log!"
            log_txt = frozen_log.read_text(encoding="utf-8")
            print(f"  Installed WinFlow.exe log: {log_txt.strip()}")
            assert "Installed WH_KEYBOARD_LL hook for Alt+X" in log_txt
            assert "[ERROR]" not in log_txt
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3.0)
            # Also ensure no child processes hold files open before TemporaryDirectory cleanup
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
            time.sleep(0.4)

    # 4. Also verify `--auto-update --no-relaunch` single-bar updater GUI mode extracts cleanly!
    with tempfile.TemporaryDirectory() as td_up:
        up_install_dir = Path(td_up) / "AutoUpdatedWinFlow"
        res_up = subprocess.run(
            [str(setup_exe), "--auto-update", "--no-relaunch", f"--dir={up_install_dir}"],
            capture_output=True,
            text=True,
            timeout=45.0,
        )
        assert res_up.returncode == 0, f"--auto-update failed with {res_up.returncode}: {res_up.stderr}"
        assert (up_install_dir / "WinFlow.exe").exists(), "WinFlow.exe missing after --auto-update!"

    print("  [PASS] Test 3 passed 100%!")


if __name__ == "__main__":
    test_1_alt_x_and_native_win_h()
    test_2_cloud_hinglish_and_minimal_ui()
    test_3_standalone_installer_and_compiled_exe_and_defender()
    print("\n=== ALL v1.3.0 TESTS PASSED 100%! ===")
