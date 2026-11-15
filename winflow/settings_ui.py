"""
Minimalist, hardware-accelerated Settings & Control Center built on native Windows Edge WebView2 (pywebview).
Runs in its own isolated STA process so clipboard paste (Ctrl+V), audio device enumeration, and UI rendering
can never freeze or contend with the background Alt+X dictation hook.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import json
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

import httpx

from .audio import list_input_devices
from .config import (
    CONFIG_PATH,
    PROJECT_ROOT,
    PROVIDER_SPECS,
    ConfigManager,
    is_startup_enabled,
    set_startup_enabled,
)
from .injector import get_clipboard_text, set_clipboard_text
from .sounds import play_sound

RELOAD_EVENT_NAME = "Local\\WinFlowReloadConfigEvent"
FOCUS_SETTINGS_EVENT_NAME = "Local\\WinFlowFocusSettingsEvent"
SETTINGS_MUTEX_NAME = "Local\\WinFlowSettingsWindowMutex"

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

EVENT_MODIFY_STATE = 0x0002


def signal_named_event(name: str) -> None:
    h = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, name)
    if h:
        kernel32.SetEvent(h)
        kernel32.CloseHandle(h)


class SettingsWindowController:
    """Spawns or focuses the isolated Settings UI process from the system tray daemon."""

    def __init__(self, config_mgr: ConfigManager, on_config_saved=None) -> None:
        self.config_mgr = config_mgr
        self.on_config_saved = on_config_saved
        self._running = False
        self._reload_event = kernel32.CreateEventW(None, False, False, RELOAD_EVENT_NAME)
        self._watch_thread: threading.Thread | None = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._watch_thread = threading.Thread(
            target=self._watch_reload_loop, name="WinFlow-ConfigWatch", daemon=True
        )
        self._watch_thread.start()

    def _watch_reload_loop(self) -> None:
        while self._running and self._reload_event:
            res = kernel32.WaitForSingleObject(self._reload_event, 500)
            if res == 0:  # WAIT_OBJECT_0
                self.config_mgr.load()
                if self.on_config_saved:
                    try:
                        self.on_config_saved()
                    except Exception:
                        pass

    def open_settings(self, tab_index: int = 0) -> None:
        # If window is already open, signal it to come to foreground immediately
        h_focus = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, FOCUS_SETTINGS_EVENT_NAME)
        if h_focus:
            kernel32.SetEvent(h_focus)
            kernel32.CloseHandle(h_focus)
            return

        python_exe = Path(sys.executable)
        # Prefer pythonw.exe so opening settings from console never flashes a terminal
        pythonw_exe = python_exe.with_name("pythonw.exe")
        exe = str(pythonw_exe if pythonw_exe.exists() else python_exe)
        subprocess.Popen(
            [exe, "-m", "winflow.settings_ui", f"--tab={tab_index}"],
            cwd=str(PROJECT_ROOT),
        )

    def stop(self) -> None:
        self._running = False
        if self._reload_event:
            kernel32.CloseHandle(self._reload_event)
            self._reload_event = None


# ============================================================================
# Minimalist HTML/CSS/JS Frontend (Linear / Raycast / Teenage Engineering craft)
# ============================================================================

MINIMALIST_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>WinFlow</title>
<style>
  :root {
    --bg: #0a0b0e;
    --surface: #12141a;
    --surface-hover: #171a22;
    --surface-2: #191c24;
    --border: rgba(255, 255, 255, 0.075);
    --border-strong: rgba(255, 255, 255, 0.15);
    --text: #f3f5f8;
    --text-secondary: #949bb0;
    --text-muted: #62697d;
    --accent: #edf0f7;
    --accent-fg: #0a0b0e;
    --blue: #5b8cff;
    --green: #34d399;
    --red: #f87171;
    --amber: #fbbf24;
    --ease-out: cubic-bezier(0.23, 1, 0.32, 1);
  }

  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    user-select: none;
    -webkit-font-smoothing: antialiased;
  }

  input, textarea {
    user-select: text;
  }

  html, body {
    height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: "Segoe UI Variable Text", "Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif;
    font-size: 13px;
    line-height: 1.45;
    overflow: hidden;
  }

  .app {
    display: flex;
    flex-direction: column;
    height: 100vh;
  }

  /* ---- Header ---- */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 22px 12px;
    flex-shrink: 0;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .logo-mark {
    width: 28px;
    height: 28px;
    border-radius: 8px;
    background: linear-gradient(160deg, #5e8bff 0%, #3b68f2 100%);
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 2px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.28);
  }

  .logo-mark span {
    display: block;
    width: 2.2px;
    border-radius: 2px;
    background: #fff;
  }

  .brand-title {
    font-size: 15px;
    font-weight: 600;
    letter-spacing: -0.02em;
  }

  .status-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 3px 9px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 500;
    background: rgba(255, 255, 255, 0.045);
    border: 1px solid var(--border);
    color: var(--text-secondary);
  }

  .status-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--green);
  }
  .status-dot.cloud { background: var(--blue); }

  .icon-btn {
    height: 30px;
    padding: 0 10px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-secondary);
    font-size: 11.5px;
    font-weight: 500;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
    transition: transform 140ms var(--ease-out), background-color 140ms ease, color 140ms ease, border-color 140ms ease;
  }
  .icon-btn:hover {
    background: var(--surface-hover);
    color: var(--text);
    border-color: var(--border-strong);
  }
  .icon-btn:active {
    transform: scale(0.96);
  }

  /* ---- Segmented Nav ---- */
  .nav-wrap {
    padding: 0 22px 12px;
    flex-shrink: 0;
  }

  .segmented-nav {
    position: relative;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 3px;
  }

  .nav-pill-bg {
    position: absolute;
    top: 3px;
    bottom: 3px;
    left: 3px;
    width: calc((100% - 6px) / 3);
    background: var(--surface-2);
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 7px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
    transition: transform 180ms var(--ease-out);
    pointer-events: none;
  }

  .nav-tab {
    position: relative;
    z-index: 1;
    background: transparent;
    border: none;
    color: var(--text-secondary);
    font-family: inherit;
    font-size: 12.5px;
    font-weight: 500;
    padding: 6px 0;
    cursor: pointer;
    border-radius: 7px;
    transition: color 140ms ease, transform 140ms var(--ease-out);
  }
  .nav-tab:hover { color: var(--text); }
  .nav-tab:active { transform: scale(0.97); }
  .nav-tab.active { color: var(--text); font-weight: 600; }

  /* ---- Scrollable Content ---- */
  .content {
    flex: 1;
    overflow-y: auto;
    padding: 2px 22px 10px;
  }
  .content::-webkit-scrollbar { width: 5px; }
  .content::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.12); border-radius: 99px; }

  .pane {
    display: none;
    flex-direction: column;
    gap: 10px;
    animation: paneEnter 160ms var(--ease-out);
  }
  .pane.active { display: flex; }

  @keyframes paneEnter {
    from { opacity: 0; transform: scale(0.988) translateY(3px); }
    to { opacity: 1; transform: scale(1) translateY(0); }
  }

  /* ---- Minimal Cards ---- */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 12px 15px;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
  }

  .card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
  }

  .card-label {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-muted);
  }

  .text-link {
    background: none;
    border: none;
    color: var(--blue);
    font-family: inherit;
    font-size: 11.5px;
    font-weight: 500;
    cursor: pointer;
    padding: 2px 4px;
    border-radius: 4px;
    transition: opacity 140ms ease, transform 140ms var(--ease-out);
  }
  .text-link:hover { opacity: 0.85; text-decoration: underline; }
  .text-link:active { transform: scale(0.96); }

  /* ---- Mode Cards ---- */
  .mode-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }

  .mode-card {
    text-align: left;
    background: rgba(255, 255, 255, 0.02);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 11px 12px;
    cursor: pointer;
    color: var(--text);
    font-family: inherit;
    transition: transform 140ms var(--ease-out), border-color 150ms ease, background-color 150ms ease;
  }
  .mode-card:hover {
    background: rgba(255, 255, 255, 0.04);
    border-color: var(--border-strong);
  }
  .mode-card:active { transform: scale(0.975); }
  .mode-card.selected {
    background: rgba(91, 140, 255, 0.08);
    border-color: rgba(91, 140, 255, 0.55);
  }
  .mode-card.selected.local-sel {
    background: rgba(52, 211, 153, 0.08);
    border-color: rgba(52, 211, 153, 0.55);
  }

  .mode-title {
    font-size: 13px;
    font-weight: 600;
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 3px;
  }
  .mode-desc {
    font-size: 11.5px;
    color: var(--text-secondary);
    line-height: 1.35;
  }

  /* ---- Provider Pills ---- */
  .pill-group {
    display: inline-flex;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 2px;
    gap: 2px;
  }

  .pill-opt {
    background: transparent;
    border: none;
    color: var(--text-secondary);
    font-family: inherit;
    font-size: 11.5px;
    font-weight: 500;
    padding: 4px 10px;
    border-radius: 6px;
    cursor: pointer;
    transition: transform 130ms var(--ease-out), background-color 140ms ease, color 140ms ease;
  }
  .pill-opt:hover { color: var(--text); }
  .pill-opt:active { transform: scale(0.96); }
  .pill-opt.active {
    background: var(--surface-2);
    color: var(--text);
    box-shadow: 0 1px 2px rgba(0,0,0,0.3);
  }

  /* ---- 8-Model Provider Grid ---- */
  .provider-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 6px;
    margin-bottom: 10px;
  }

  .prov-chip {
    height: 30px;
    padding: 0 8px;
    border-radius: 7px;
    border: 1px solid var(--border);
    background: rgba(255, 255, 255, 0.02);
    color: var(--text-secondary);
    font-family: inherit;
    font-size: 11.5px;
    font-weight: 500;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 5px;
    cursor: pointer;
    transition: transform 130ms var(--ease-out), border-color 140ms ease, background-color 140ms ease, color 140ms ease;
  }
  .prov-chip:hover {
    color: var(--text);
    background: rgba(255, 255, 255, 0.045);
    border-color: var(--border-strong);
  }
  .prov-chip:active {
    transform: scale(0.96);
  }
  .prov-chip.active {
    background: rgba(91, 140, 255, 0.13);
    border-color: rgba(91, 140, 255, 0.58);
    color: var(--text);
    font-weight: 600;
  }
  .prov-dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--green);
    display: none;
    flex-shrink: 0;
  }
  .prov-chip.has-key .prov-dot {
    display: inline-block;
  }

  .custom-endpoint-row {
    display: none;
    grid-template-columns: 1.4fr 1fr;
    gap: 7px;
    margin-top: 8px;
  }
  .custom-endpoint-row.visible {
    display: grid;
  }

  /* ---- API Key Input + Inline Apply Button ---- */
  .key-row {
    display: flex;
    align-items: center;
    gap: 7px;
    margin-top: 10px;
  }

  .input-box {
    flex: 1;
    display: flex;
    align-items: center;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0 6px 0 11px;
    height: 36px;
    transition: border-color 140ms ease;
  }
  .input-box:focus-within {
    border-color: rgba(91, 140, 255, 0.65);
  }

  .input-box input {
    flex: 1;
    background: transparent;
    border: none;
    outline: none;
    color: var(--text);
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 12px;
    min-width: 0;
  }
  .input-box input::placeholder {
    color: var(--text-muted);
    font-family: "Segoe UI", sans-serif;
  }

  .mini-btn {
    background: rgba(255, 255, 255, 0.055);
    border: 1px solid transparent;
    color: var(--text-secondary);
    font-family: inherit;
    font-size: 11px;
    font-weight: 500;
    height: 24px;
    padding: 0 8px;
    border-radius: 5px;
    cursor: pointer;
    margin-left: 4px;
    transition: transform 130ms var(--ease-out), background-color 130ms ease, color 130ms ease;
  }
  .mini-btn:hover { background: rgba(255, 255, 255, 0.1); color: var(--text); }
  .mini-btn:active { transform: scale(0.95); }

  .btn-primary {
    height: 36px;
    padding: 0 15px;
    border-radius: 8px;
    border: none;
    background: var(--accent);
    color: var(--accent-fg);
    font-family: inherit;
    font-size: 12.5px;
    font-weight: 600;
    cursor: pointer;
    white-space: nowrap;
    transition: transform 140ms var(--ease-out), opacity 140ms ease, background-color 140ms ease;
  }
  .btn-primary:hover { opacity: 0.92; }
  .btn-primary:active { transform: scale(0.96); }
  .btn-primary:disabled { opacity: 0.5; cursor: default; transform: none; }

  .key-feedback {
    margin-top: 8px;
    font-size: 11.5px;
    min-height: 17px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    color: var(--text-muted);
  }
  .key-feedback.ok { color: var(--green); }
  .key-feedback.err { color: var(--red); }

  /* ---- Setting Rows ---- */
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 14px;
    padding: 6.5px 0;
  }
  .row + .row {
    border-top: 1px solid rgba(255, 255, 255, 0.05);
  }

  .row-title {
    font-size: 13px;
    font-weight: 500;
    color: var(--text);
  }
  .row-sub {
    font-size: 11.5px;
    color: var(--text-muted);
    margin-top: 1px;
  }

  /* ---- Minimal Switch ---- */
  .switch {
    position: relative;
    width: 38px;
    height: 22px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.12);
    border: none;
    cursor: pointer;
    flex-shrink: 0;
    transition: background-color 160ms ease, transform 130ms var(--ease-out);
  }
  .switch:active { transform: scale(0.94); }
  .switch.on { background: var(--blue); }
  .switch::after {
    content: "";
    position: absolute;
    top: 3px;
    left: 3px;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: #fff;
    box-shadow: 0 1px 2px rgba(0,0,0,0.35);
    transition: transform 160ms var(--ease-out);
  }
  .switch.on::after {
    transform: translateX(16px);
  }

  /* ---- Minimal Select ---- */
  select.clean-select {
    background: var(--bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    height: 32px;
    padding: 0 10px;
    font-family: inherit;
    font-size: 12px;
    outline: none;
    cursor: pointer;
    max-width: 230px;
  }
  select.clean-select:focus { border-color: var(--border-strong); }

  /* ---- History List ---- */
  .hist-item {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 11px;
    padding: 12px 14px;
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }
  .hist-meta {
    font-size: 11px;
    color: var(--text-muted);
    font-variant-numeric: tabular-nums;
    margin-bottom: 4px;
  }
  .hist-text {
    font-size: 13px;
    color: var(--text);
    white-space: pre-wrap;
    word-break: break-word;
    user-select: text;
  }
  .empty-state {
    text-align: center;
    padding: 48px 20px;
    color: var(--text-muted);
    font-size: 13px;
  }

  /* ---- Sticky Footer (Never clips at any DPI!) ---- */
  .footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 11px 22px 14px;
    border-top: 1px solid var(--border);
    background: rgba(10, 11, 14, 0.92);
    flex-shrink: 0;
  }

  .kbd-hint {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11.5px;
    color: var(--text-muted);
  }

  kbd {
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 10.5px;
    padding: 2px 6px;
    border-radius: 5px;
    background: var(--surface-2);
    border: 1px solid var(--border-strong);
    color: var(--text-secondary);
  }
</style>
</head>
<body>
<div class="app">
  <!-- Header -->
  <header class="header">
    <div class="brand">
      <div class="logo-mark">
        <span style="height:8px"></span>
        <span style="height:15px"></span>
        <span style="height:11px"></span>
        <span style="height:6px"></span>
      </div>
      <span class="brand-title">WinFlow</span>
      <span class="status-chip" id="headerStatus">
        <span class="status-dot" id="headerDot"></span>
        <span id="headerModeText">Ready</span>
      </span>
    </div>
    <button class="icon-btn" id="btnSoundToggle" onclick="toggleUiSounds()" title="Toggle tactile button sounds">
      <svg id="soundSvgOn" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path><path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path></svg>
      <svg id="soundSvgOff" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="display:none"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><line x1="23" y1="9" x2="17" y2="15"></line><line x1="17" y1="9" x2="23" y2="15"></line></svg>
      <span id="soundLabel">Sound On</span>
    </button>
  </header>

  <!-- Segmented Navigation -->
  <div class="nav-wrap">
    <div class="segmented-nav">
      <div class="nav-pill-bg" id="navIndicator"></div>
      <button class="nav-tab active" onclick="switchTab(0)">Engine</button>
      <button class="nav-tab" onclick="switchTab(1)">Preferences</button>
      <button class="nav-tab" onclick="switchTab(2)">History</button>
    </div>
  </div>

  <!-- Scrollable Main Area -->
  <main class="content">
    <!-- TAB 0: ENGINE, HINGLISH & MULTI-MODEL API KEYS -->
    <section class="pane active" id="pane-0">
      <div class="card">
        <div class="card-head">
          <span class="card-label">Dictation Engine & Language</span>
        </div>
        <div class="mode-grid">
          <button class="mode-card" id="cardCloud" onclick="selectEngineMode('cloud')">
            <div class="mode-title">
              <span>Cloud AI</span>
              <span style="font-size:11px;color:var(--blue)">~0.8s</span>
            </div>
            <div class="mode-desc">Instant Whisper + AI filler & Hinglish cleanup. Zero PC load.</div>
          </button>
          <button class="mode-card local-sel" id="cardLocal" onclick="selectEngineMode('local')">
            <div class="mode-title">
              <span>Offline Local</span>
              <span style="font-size:11px;color:var(--green)">Private</span>
            </div>
            <div class="mode-desc">Runs faster-whisper locally on your PC. No API key needed.</div>
          </button>
        </div>

        <div class="row" style="margin-top:10px;padding-top:9px;padding-bottom:0;border-top:1px solid rgba(255,255,255,0.055)">
          <div>
            <div class="row-title">Dictation Language</div>
            <div class="row-sub" id="langSubHint">English-lock prevents foreign script hallucinations</div>
          </div>
          <div class="pill-group">
            <button class="pill-opt" data-lang="en" onclick="setLanguage('en')">English</button>
            <button class="pill-opt" data-lang="hinglish" onclick="setLanguage('hinglish')" title="Speak Hindi + English -> writes in English letters like 'Apna'">Hinglish</button>
            <button class="pill-opt" data-lang="hi" onclick="setLanguage('hi')">Hindi</button>
            <button class="pill-opt" data-lang="auto" onclick="setLanguage('auto')">Auto</button>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head" style="margin-bottom:8px">
          <span class="card-label" style="margin:0">AI Model Provider & API Key</span>
          <button class="text-link" id="btnGetKey" onclick="openKeyPortal()">Get free Groq key ↗</button>
        </div>

        <div class="provider-grid">
          <button class="prov-chip" data-prov="groq" onclick="selectProvider('groq')"><span class="prov-dot"></span><span>Groq</span></button>
          <button class="prov-chip" data-prov="gemini" onclick="selectProvider('gemini')"><span class="prov-dot"></span><span>Gemini</span></button>
          <button class="prov-chip" data-prov="openai" onclick="selectProvider('openai')"><span class="prov-dot"></span><span>OpenAI GPT</span></button>
          <button class="prov-chip" data-prov="anthropic" onclick="selectProvider('anthropic')"><span class="prov-dot"></span><span>Claude</span></button>
          <button class="prov-chip" data-prov="xai" onclick="selectProvider('xai')"><span class="prov-dot"></span><span>xAI Grok</span></button>
          <button class="prov-chip" data-prov="nvidia" onclick="selectProvider('nvidia')"><span class="prov-dot"></span><span>NVIDIA</span></button>
          <button class="prov-chip" data-prov="kimi" onclick="selectProvider('kimi')"><span class="prov-dot"></span><span>Kimi</span></button>
          <button class="prov-chip" data-prov="custom" onclick="selectProvider('custom')"><span class="prov-dot"></span><span>Custom +</span></button>
        </div>

        <div class="custom-endpoint-row" id="customEndpointRow">
          <div class="input-box" style="height:32px">
            <input
              type="text"
              id="customBaseUrlInput"
              placeholder="https://openrouter.ai/api/v1"
              spellcheck="false"
              oninput="state.custom_base_url = this.value.trim()"
            />
          </div>
          <div class="input-box" style="height:32px">
            <input
              type="text"
              id="customModelInput"
              placeholder="Model (e.g. openai/gpt-4o-mini)"
              spellcheck="false"
              oninput="state.custom_model = this.value.trim()"
            />
          </div>
        </div>

        <div class="key-row" style="margin-top:8px">
          <div class="input-box">
            <input
              type="password"
              id="apiKeyInput"
              placeholder="Paste gsk_... key here"
              autocomplete="off"
              spellcheck="false"
              oninput="onKeyInputChanged()"
              onkeydown="if(event.key==='Enter'){applyApiKey();}"
            />
            <button class="mini-btn" onclick="pasteFromClipboard()" title="Paste from clipboard">Paste</button>
            <button class="mini-btn" id="btnReveal" onclick="toggleRevealKey()" title="Show or hide key">Show</button>
          </div>
          <button class="btn-primary" id="btnApplyKey" onclick="applyApiKey()">Apply Key</button>
        </div>

        <div class="key-feedback" id="keyFeedback">
          <span id="keyStatusMsg">Free Groq key runs Whisper Large v3 Turbo + Qwen 3.8 (~0.8s).</span>
        </div>
      </div>

      <div class="card">
        <div class="row" style="padding:0">
          <div>
            <div class="row-title">Offline Whisper Model</div>
            <div class="row-sub">Runs 100% locally in Offline mode (auto-unloaded from RAM in Cloud mode)</div>
          </div>
          <div class="pill-group">
            <button class="pill-opt" data-model="tiny" onclick="setLocalModel('tiny')">Tiny</button>
            <button class="pill-opt" data-model="base" onclick="setLocalModel('base')">Base</button>
            <button class="pill-opt" data-model="small" onclick="setLocalModel('small')">Small</button>
          </div>
        </div>
      </div>
    </section>

    <!-- TAB 1: PREFERENCES -->
    <section class="pane" id="pane-1">
      <div class="card">
        <div class="card-head" style="margin-bottom:4px">
          <span class="card-label">AI Speech Cleanup</span>
        </div>

        <div class="row">
          <div>
            <div class="row-title">Remove Filler Words & Stutters</div>
            <div class="row-sub">Strips "um", "uh", "you know" and repeats like "can Can"</div>
          </div>
          <button class="switch" id="swFillers" onclick="toggleBool('remove_fillers', 'swFillers')"></button>
        </div>

        <div class="row">
          <div>
            <div class="row-title">Smart Punctuation & Voice Breaks</div>
            <div class="row-sub">Auto-capitalizes and turns "new line" into line breaks</div>
          </div>
          <button class="switch" id="swSmartFmt" onclick="toggleBool('smart_formatting', 'swSmartFmt')"></button>
        </div>

        <div class="row">
          <div>
            <div class="row-title">Writing Style</div>
            <div class="row-sub">Natural preserves your exact wording</div>
          </div>
          <div class="pill-group">
            <button class="pill-opt" data-style="clean" onclick="setStyleMode('clean')">Natural</button>
            <button class="pill-opt" data-style="polished" onclick="setStyleMode('polished')">Polished</button>
            <button class="pill-opt" data-style="raw" onclick="setStyleMode('raw')">Verbatim</button>
          </div>
        </div>

        <div class="row" style="flex-direction:column;align-items:stretch;gap:7px">
          <div style="display:flex;justify-content:space-between;align-items:center">
            <span class="row-title">Custom Vocabulary</span>
            <span class="row-sub" style="margin:0">Comma-separated names & jargon</span>
          </div>
          <div class="key-row" style="margin-top:0">
            <div class="input-box" style="height:34px">
              <input
                type="text"
                id="dictInput"
                style="font-family:inherit"
                placeholder="Wispr Flow, WinFlow, GitHub, Windows"
                oninput="state.custom_dictionary = this.value"
                onkeydown="if(event.key==='Enter'){saveAllSettings(true);}"
              />
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-head" style="margin-bottom:4px">
          <span class="card-label">Shortcut & System</span>
        </div>

        <div class="row">
          <div>
            <div class="row-title">Alt + X Trigger Mode</div>
            <div class="row-sub">Hybrid: quick tap toggles, holding acts as push-to-talk</div>
          </div>
          <div class="pill-group">
            <button class="pill-opt" data-hk="hybrid" onclick="setHotkeyMode('hybrid')">Hybrid</button>
            <button class="pill-opt" data-hk="toggle" onclick="setHotkeyMode('toggle')">Toggle</button>
            <button class="pill-opt" data-hk="hold" onclick="setHotkeyMode('hold')">Hold</button>
          </div>
        </div>

        <div class="row">
          <div>
            <div class="row-title">Microphone</div>
            <div class="row-sub">Audio input device</div>
          </div>
          <select class="clean-select" id="micSelect" onchange="onMicChanged()"></select>
        </div>

        <div class="row">
          <div>
            <div class="row-title">Restore Previous Clipboard</div>
            <div class="row-sub">Preserves copied items after pasting dictation</div>
          </div>
          <button class="switch" id="swClipboard" onclick="toggleBool('restore_clipboard', 'swClipboard')"></button>
        </div>

        <div class="row">
          <div>
            <div class="row-title">Dictation Start / Stop Chimes</div>
            <div class="row-sub">Subtle acoustic cue on Alt + X</div>
          </div>
          <button class="switch" id="swCues" onclick="toggleBool('sound_cues', 'swCues')"></button>
        </div>

        <div class="row" style="padding-bottom:2px">
          <div>
            <div class="row-title">Launch at Windows Sign-In</div>
            <div class="row-sub">Start silently in system tray on boot</div>
          </div>
          <button class="switch" id="swStartup" onclick="toggleStartup()"></button>
        </div>
      </div>
    </section>

    <!-- TAB 2: HISTORY -->
    <section class="pane" id="pane-2">
      <div id="historyContainer" style="display:flex;flex-direction:column;gap:8px"></div>
    </section>
  </main>

  <!-- Sticky Footer (Always visible on every screen & DPI scale!) -->
  <footer class="footer">
    <div class="kbd-hint">
      <kbd>Alt</kbd> + <kbd>X</kbd> <span>WinFlow</span>
      <span style="opacity:0.35">•</span>
      <kbd>Win</kbd> + <kbd>H</kbd> <span>Native intact</span>
    </div>
    <button class="btn-primary" id="btnSaveAll" onclick="saveAllSettings(true)">Apply & Save</button>
  </footer>
</div>

<script>
  // ---- Precision Web Audio Micro-Acoustics ----
  let audioCtx = null;
  const PROVIDERS = {
    groq: {
      badge: "Groq Turbo",
      placeholder: "Paste gsk_... Groq API key",
      portalLabel: "Get free Groq key ↗",
      portalUrl: "https://console.groq.com/keys",
      hint: "Free Groq key runs Whisper Large v3 Turbo + Qwen 3.8 (~0.8s)."
    },
    gemini: {
      badge: "Gemini Flash",
      placeholder: "Paste AIza... / AQ... Gemini API key",
      portalLabel: "Get free Gemini key ↗",
      portalUrl: "https://aistudio.google.com/apikey",
      hint: "Google Gemini 3.5 Flash Lite single-pass audio & Hinglish cleanup."
    },
    openai: {
      badge: "OpenAI GPT",
      placeholder: "Paste sk-... OpenAI GPT API key",
      portalLabel: "Get OpenAI key ↗",
      portalUrl: "https://platform.openai.com/api-keys",
      hint: "OpenAI GPT-4o Mini Transcribe + GPT-4o Mini cleanup."
    },
    anthropic: {
      badge: "Anthropic Claude",
      placeholder: "Paste sk-ant-... Claude API key",
      portalLabel: "Get Claude key ↗",
      portalUrl: "https://console.anthropic.com/settings/keys",
      hint: "Anthropic Claude Haiku / Sonnet cleanup & Hinglish Romanizer."
    },
    xai: {
      badge: "xAI Grok",
      placeholder: "Paste xai-... Elon Musk's Grok API key",
      portalLabel: "Get xAI Grok key ↗",
      portalUrl: "https://console.x.ai/",
      hint: "Elon Musk's xAI Grok fast cleanup & Hinglish Romanizer."
    },
    nvidia: {
      badge: "NVIDIA NIM",
      placeholder: "Paste nvapi-... NVIDIA NIM API key",
      portalLabel: "Get free NVIDIA key ↗",
      portalUrl: "https://build.nvidia.com/explore/discover",
      hint: "NVIDIA Build NIM (Llama / Nemotron / Qwen) cleanup & Hinglish."
    },
    kimi: {
      badge: "Moonshot Kimi",
      placeholder: "Paste sk-... Moonshot Kimi API key",
      portalLabel: "Get Kimi key ↗",
      portalUrl: "https://platform.moonshot.ai/console/api-keys",
      hint: "Moonshot AI Kimi K2 cleanup & Hinglish (.ai & .cn supported)."
    },
    custom: {
      badge: "Custom API",
      placeholder: "Paste sk-... OpenRouter / DeepSeek / Custom key",
      portalLabel: "OpenRouter keys ↗",
      portalUrl: "https://openrouter.ai/keys",
      hint: "Any OpenAI-compatible API (OpenRouter, DeepSeek, Together, Ollama)."
    }
  };

  let state = {
    engine_mode: "local",
    cloud_provider: "groq",
    api_keys: {},
    custom_base_url: "https://openrouter.ai/api/v1",
    custom_model: "",
    local_model_size: "base",
    language: "en",
    hotkey_mode: "hybrid",
    remove_fillers: true,
    smart_formatting: true,
    style_mode: "clean",
    custom_dictionary: "",
    restore_clipboard: true,
    sound_cues: true,
    ui_sounds: true,
    input_device: null,
    startup: false,
    history: []
  };

  function getCtx() {
    if (!audioCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) audioCtx = new AC();
    }
    if (audioCtx && audioCtx.state === "suspended") audioCtx.resume();
    return audioCtx;
  }

  function uiTone(kind) {
    if (!state.ui_sounds && kind !== "unmute") return;
    try {
      const ctx = getCtx();
      if (!ctx) return;
      const now = ctx.currentTime;

      if (kind === "press" || kind === "unmute") {
        // Tactile mechanical switch click (15ms)
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "triangle";
        osc.frequency.setValueAtTime(1350, now);
        osc.frequency.exponentialRampToValueAtTime(340, now + 0.016);
        gain.gain.setValueAtTime(0.13, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.018);
        osc.connect(gain).connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.02);
      } else if (kind === "tab") {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(920, now);
        osc.frequency.exponentialRampToValueAtTime(560, now + 0.013);
        gain.gain.setValueAtTime(0.08, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.015);
        osc.connect(gain).connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.016);
      } else if (kind === "toggleOn") {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(540, now);
        osc.frequency.setValueAtTime(810, now + 0.016);
        gain.gain.setValueAtTime(0.09, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.042);
        osc.connect(gain).connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.045);
      } else if (kind === "toggleOff") {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(680, now);
        osc.frequency.setValueAtTime(460, now + 0.015);
        gain.gain.setValueAtTime(0.08, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.038);
        osc.connect(gain).connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.04);
      } else if (kind === "apply") {
        // Warm fifth chord (F5 + C6)
        [698.46, 1046.5].forEach((freq, idx) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = "sine";
          osc.frequency.setValueAtTime(freq, now + idx * 0.018);
          gain.gain.setValueAtTime(0.09, now + idx * 0.018);
          gain.gain.exponentialRampToValueAtTime(0.001, now + 0.13);
          osc.connect(gain).connect(ctx.destination);
          osc.start(now + idx * 0.018);
          osc.stop(now + 0.135);
        });
      } else if (kind === "error") {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = "sine";
        osc.frequency.setValueAtTime(240, now);
        osc.frequency.setValueAtTime(185, now + 0.035);
        gain.gain.setValueAtTime(0.11, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.085);
        osc.connect(gain).connect(ctx.destination);
        osc.start(now);
        osc.stop(now + 0.09);
      }
    } catch (e) {}
  }

  function switchTab(idx, silent = false) {
    if (!silent) uiTone("tab");
    document.getElementById("navIndicator").style.transform = `translateX(${idx * 100}%)`;
    document.querySelectorAll(".nav-tab").forEach((b, i) => b.classList.toggle("active", i === idx));
    document.querySelectorAll(".pane").forEach((p, i) => p.classList.toggle("active", i === idx));
    if (idx === 2 && window.pywebview && window.pywebview.api) {
      window.pywebview.api.get_state().then(s => {
        state.history = s.history || [];
        renderHistory();
      });
    }
  }

  function getProviderKey(prov) {
    const p = prov || state.cloud_provider;
    return ((state.api_keys && state.api_keys[p]) || "").trim();
  }

  function syncKeyInputFromProvider() {
    const spec = PROVIDERS[state.cloud_provider] || PROVIDERS.groq;
    const inp = document.getElementById("apiKeyInput");
    const btnLink = document.getElementById("btnGetKey");
    inp.value = getProviderKey(state.cloud_provider);
    inp.placeholder = spec.placeholder;
    btnLink.textContent = spec.portalLabel;
    document.getElementById("customBaseUrlInput").value = state.custom_base_url || "https://openrouter.ai/api/v1";
    document.getElementById("customModelInput").value = state.custom_model || "";
  }

  function renderUI() {
    const spec = PROVIDERS[state.cloud_provider] || PROVIDERS.groq;
    // Header chip
    const dot = document.getElementById("headerDot");
    const txt = document.getElementById("headerModeText");
    const langSuffix = state.language === "hinglish" ? " • Hinglish" : "";
    if (state.engine_mode === "cloud") {
      dot.className = "status-dot cloud";
      txt.textContent = spec.badge + langSuffix;
    } else {
      dot.className = "status-dot";
      txt.textContent = `Offline (${state.local_model_size})${langSuffix}`;
    }

    // Language subtitle helper
    const langHints = {
      en: "English-lock prevents foreign script hallucinations",
      hinglish: 'Hindi spoken → English letters (e.g. "Bhai apna kaam kar lo")',
      hi: "Outputs native Devanagari Hindi script (अपना काम कर लो)",
      auto: "Automatically detects spoken language"
    };
    const langSub = document.getElementById("langSubHint");
    if (langSub) {
      langSub.textContent = langHints[state.language] || langHints.en;
      langSub.style.color = state.language === "hinglish" ? "var(--blue)" : "var(--text-muted)";
    }

    // Sound toggle button
    document.getElementById("soundSvgOn").style.display = state.ui_sounds ? "block" : "none";
    document.getElementById("soundSvgOff").style.display = state.ui_sounds ? "none" : "block";
    document.getElementById("soundLabel").textContent = state.ui_sounds ? "Sound On" : "Muted";

    // Mode cards
    document.getElementById("cardCloud").classList.toggle("selected", state.engine_mode === "cloud");
    document.getElementById("cardLocal").classList.toggle("selected", state.engine_mode === "local");

    // 8-Provider grid chips + saved key emerald dot
    document.querySelectorAll("[data-prov]").forEach(el => {
      const p = el.dataset.prov;
      el.classList.toggle("active", p === state.cloud_provider);
      el.classList.toggle("has-key", !!getProviderKey(p));
    });

    // Custom endpoint fields visibility
    document.getElementById("customEndpointRow").classList.toggle("visible", state.cloud_provider === "custom");

    // Pill groups
    document.querySelectorAll("[data-model]").forEach(el =>
      el.classList.toggle("active", el.dataset.model === state.local_model_size)
    );
    document.querySelectorAll("[data-lang]").forEach(el =>
      el.classList.toggle("active", el.dataset.lang === state.language)
    );
    document.querySelectorAll("[data-style]").forEach(el =>
      el.classList.toggle("active", el.dataset.style === state.style_mode)
    );
    document.querySelectorAll("[data-hk]").forEach(el =>
      el.classList.toggle("active", el.dataset.hk === state.hotkey_mode)
    );

    // Switches
    document.getElementById("swFillers").classList.toggle("on", !!state.remove_fillers);
    document.getElementById("swSmartFmt").classList.toggle("on", !!state.smart_formatting);
    document.getElementById("swClipboard").classList.toggle("on", !!state.restore_clipboard);
    document.getElementById("swCues").classList.toggle("on", !!state.sound_cues);
    document.getElementById("swStartup").classList.toggle("on", !!state.startup);

    const dicEl = document.getElementById("dictInput");
    if (document.activeElement !== dicEl) {
      dicEl.value = state.custom_dictionary || "";
    }
    renderHistory();
  }

  function renderHistory() {
    const box = document.getElementById("historyContainer");
    if (!state.history || state.history.length === 0) {
      box.innerHTML = `<div class="empty-state">No dictations yet.<br/>Press <kbd>Alt</kbd> + <kbd>X</kbd> in any app and speak naturally.</div>`;
      return;
    }
    box.innerHTML = "";
    state.history.slice(0, 25).forEach((item, idx) => {
      const div = document.createElement("div");
      div.className = "hist-item";
      const left = document.createElement("div");
      left.style.flex = "1";
      const meta = document.createElement("div");
      meta.className = "hist-meta";
      meta.textContent = `${item.timestamp || ""}  •  ${item.mode || ""}  •  ${item.latency_ms || 0} ms`;
      const body = document.createElement("div");
      body.className = "hist-text";
      body.textContent = item.cleaned || "";
      left.appendChild(meta);
      left.appendChild(body);

      const btn = document.createElement("button");
      btn.className = "icon-btn";
      btn.textContent = "Copy";
      btn.onclick = async () => {
        uiTone("press");
        await window.pywebview.api.copy_text(item.cleaned || "");
        btn.textContent = "Copied ✓";
        setTimeout(() => { btn.textContent = "Copy"; }, 1100);
      };
      div.appendChild(left);
      div.appendChild(btn);
      box.appendChild(div);
    });
  }

  function setFeedback(msg, kind = "") {
    const box = document.getElementById("keyFeedback");
    box.className = "key-feedback " + kind;
    document.getElementById("keyStatusMsg").textContent = msg;
  }

  function captureCurrentKeyInput() {
    const val = document.getElementById("apiKeyInput").value.trim();
    if (!state.api_keys) state.api_keys = {};
    state.api_keys[state.cloud_provider] = val;
    return val;
  }

  function onKeyInputChanged() {
    const val = captureCurrentKeyInput();
    renderUI();
    if (val) {
      setFeedback("Click Apply Key (or press Enter) to verify & activate.", "");
    }
  }

  function selectProvider(prov) {
    captureCurrentKeyInput();
    uiTone("tab");
    state.cloud_provider = prov;
    syncKeyInputFromProvider();
    renderUI();
    const spec = PROVIDERS[prov] || PROVIDERS.groq;
    const hasKey = !!getProviderKey(prov);
    setFeedback(
      hasKey ? `✓ ${spec.badge} key saved — click Apply Key to switch & verify.` : spec.hint,
      hasKey ? "ok" : ""
    );
  }

  async function selectEngineMode(mode) {
    captureCurrentKeyInput();
    if (mode === "cloud") {
      if (!getProviderKey(state.cloud_provider)) {
        uiTone("error");
        setFeedback("Paste an API key below and click Apply Key first.", "err");
        document.getElementById("apiKeyInput").focus();
        return;
      }
    }
    uiTone("press");
    state.engine_mode = mode;
    renderUI();
    await saveAllSettings(false);
  }

  async function pasteFromClipboard() {
    uiTone("press");
    if (!window.pywebview) return;
    const clip = await window.pywebview.api.read_clipboard();
    if (clip) {
      const inp = document.getElementById("apiKeyInput");
      inp.value = clip.trim();
      captureCurrentKeyInput();
      renderUI();
      setFeedback("Key pasted — click Apply Key to activate.", "");
    }
  }

  function toggleRevealKey() {
    uiTone("tab");
    const inp = document.getElementById("apiKeyInput");
    const btn = document.getElementById("btnReveal");
    if (inp.type === "password") {
      inp.type = "text";
      btn.textContent = "Hide";
    } else {
      inp.type = "password";
      btn.textContent = "Show";
    }
  }

  function openKeyPortal() {
    uiTone("press");
    const spec = PROVIDERS[state.cloud_provider] || PROVIDERS.groq;
    window.pywebview.api.open_url(spec.portalUrl);
  }

  async function applyApiKey() {
    const keyVal = captureCurrentKeyInput();
    state.custom_base_url = document.getElementById("customBaseUrlInput").value.trim() || "https://openrouter.ai/api/v1";
    state.custom_model = document.getElementById("customModelInput").value.trim();
    if (!keyVal) {
      uiTone("error");
      setFeedback("Please paste an API key first.", "err");
      document.getElementById("apiKeyInput").focus();
      return;
    }

    uiTone("press");
    const btn = document.getElementById("btnApplyKey");
    btn.disabled = true;
    btn.textContent = "Checking…";
    setFeedback("Verifying API key…", "");

    try {
      const res = await window.pywebview.api.verify_and_apply_key(
        state.cloud_provider,
        keyVal,
        state.custom_base_url,
        state.custom_model
      );
      if (res.ok) {
        state.engine_mode = "cloud";
        renderUI();
        uiTone("apply");
        btn.textContent = "Applied ✓";
        setFeedback(res.message || "Connected! Active on Alt + X.", "ok");
        setTimeout(() => { btn.textContent = "Apply Key"; btn.disabled = false; }, 1300);
      } else {
        uiTone("error");
        btn.textContent = "Apply Key";
        btn.disabled = false;
        setFeedback(res.message || "Invalid API key — please check and try again.", "err");
      }
    } catch (err) {
      btn.textContent = "Apply Key";
      btn.disabled = false;
      uiTone("error");
      setFeedback("Could not verify key right now.", "err");
    }
  }

  function toggleUiSounds() {
    const turningOn = !state.ui_sounds;
    state.ui_sounds = turningOn;
    if (turningOn) uiTone("unmute");
    renderUI();
    saveAllSettings(false);
  }

  function toggleBool(key, elId) {
    state[key] = !state[key];
    uiTone(state[key] ? "toggleOn" : "toggleOff");
    renderUI();
    saveAllSettings(false);
  }

  async function toggleStartup() {
    state.startup = !state.startup;
    uiTone(state.startup ? "toggleOn" : "toggleOff");
    renderUI();
    await window.pywebview.api.set_startup(state.startup);
  }

  function setLocalModel(m) {
    uiTone("tab");
    state.local_model_size = m;
    renderUI();
    saveAllSettings(false);
  }

  function setLanguage(l) {
    uiTone("tab");
    state.language = l;
    renderUI();
    saveAllSettings(false);
  }

  function setStyleMode(s) {
    uiTone("tab");
    state.style_mode = s;
    renderUI();
    saveAllSettings(false);
  }

  function setHotkeyMode(h) {
    uiTone("tab");
    state.hotkey_mode = h;
    renderUI();
    saveAllSettings(false);
  }

  function onMicChanged() {
    uiTone("tab");
    const sel = document.getElementById("micSelect");
    state.input_device = sel.value === "" ? null : parseInt(sel.value, 10);
    saveAllSettings(false);
  }

  async function saveAllSettings(explicitClick = false) {
    captureCurrentKeyInput();
    state.custom_dictionary = document.getElementById("dictInput").value.trim();
    state.custom_base_url = document.getElementById("customBaseUrlInput").value.trim() || "https://openrouter.ai/api/v1";
    state.custom_model = document.getElementById("customModelInput").value.trim();

    if (explicitClick) {
      if (getProviderKey(state.cloud_provider) && state.engine_mode === "local") {
        state.engine_mode = "cloud";
      }
    }

    if (window.pywebview && window.pywebview.api) {
      await window.pywebview.api.save_state(state);
    }
    renderUI();

    if (explicitClick) {
      uiTone("apply");
      const btn = document.getElementById("btnSaveAll");
      btn.textContent = "Applied ✓";
      setFeedback("All settings saved & active on Alt + X.", "ok");
      setTimeout(() => { btn.textContent = "Apply & Save"; }, 1200);
    }
  }

  window.addEventListener("pywebviewready", async () => {
    const initial = await window.pywebview.api.get_state();
    Object.assign(state, initial);
    if (!state.api_keys) state.api_keys = {};
    syncKeyInputFromProvider();
    renderUI();
    if (initial.initial_tab) {
      switchTab(initial.initial_tab, true);
    }
    const spec = PROVIDERS[state.cloud_provider] || PROVIDERS.groq;
    if (getProviderKey(state.cloud_provider) && state.engine_mode === "cloud") {
      setFeedback(`✓ ${spec.badge} API key active on Alt + X.`, "ok");
    } else {
      setFeedback(spec.hint, "");
    }

    // Load microphones asynchronously so slow Bluetooth audio drivers NEVER block UI startup
    const mics = await window.pywebview.api.get_microphones();
    const sel = document.getElementById("micSelect");
    sel.innerHTML = "";
    mics.forEach(([id, name]) => {
      const opt = document.createElement("option");
      opt.value = id === null ? "" : String(id);
      opt.textContent = name;
      if (id === state.input_device || (id === null && state.input_device === null)) {
        opt.selected = true;
      }
      sel.appendChild(opt);
    });
  });
</script>
</body>
</html>
"""


class _SettingsJsApi:
    def __init__(self, config_mgr: ConfigManager, initial_tab: int = 0) -> None:
        self.config_mgr = config_mgr
        self.initial_tab = initial_tab

    def get_state(self) -> dict[str, Any]:
        cfg = self.config_mgr.load()
        return {
            "engine_mode": cfg.engine_mode,
            "cloud_provider": cfg.cloud_provider,
            "api_keys": dict(cfg.api_keys),
            "custom_base_url": cfg.custom_base_url,
            "custom_model": cfg.custom_model,
            "local_model_size": cfg.local_model_size,
            "language": cfg.language,
            "hotkey_mode": cfg.hotkey_mode,
            "remove_fillers": cfg.remove_fillers,
            "smart_formatting": cfg.smart_formatting,
            "style_mode": cfg.style_mode,
            "custom_dictionary": cfg.custom_dictionary,
            "restore_clipboard": cfg.restore_clipboard,
            "sound_cues": cfg.sound_cues,
            "ui_sounds": cfg.ui_sounds,
            "input_device": cfg.input_device,
            "startup": is_startup_enabled(),
            "history": cfg.history,
            "initial_tab": self.initial_tab,
        }

    def get_microphones(self) -> list[list[Any]]:
        return [[dev_id, name] for dev_id, name in list_input_devices()]

    def read_clipboard(self) -> str:
        return get_clipboard_text() or ""

    def copy_text(self, text: str) -> bool:
        return set_clipboard_text(text)

    def open_url(self, url: str) -> None:
        webbrowser.open(url)

    def set_startup(self, enable: bool) -> bool:
        return set_startup_enabled(bool(enable))

    def verify_and_apply_key(
        self,
        provider: str,
        api_key: str,
        custom_base_url: str = "",
        custom_model: str = "",
    ) -> dict[str, Any]:
        key = (api_key or "").strip()
        if not key:
            return {"ok": False, "message": "API key cannot be empty."}

        badge = PROVIDER_SPECS.get(provider, {}).get("badge", provider.upper())
        verified_msg = f"✓ {badge} verified & activated on Alt + X!"

        try:
            with httpx.Client(timeout=4.5, follow_redirects=True) as client:
                if provider == "gemini":
                    r = client.get(
                        "https://generativelanguage.googleapis.com/v1beta/models",
                        headers={"x-goog-api-key": key},
                    )
                    if r.status_code in (400, 401, 403):
                        return {"ok": False, "message": f"Gemini rejected this key ({r.status_code})."}
                elif provider == "anthropic":
                    r = client.get(
                        "https://api.anthropic.com/v1/models",
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    )
                    if r.status_code in (401, 403):
                        return {"ok": False, "message": "Anthropic rejected this Claude API key."}
                elif provider == "kimi":
                    r = client.get(
                        "https://api.moonshot.ai/v1/models",
                        headers={"Authorization": f"Bearer {key}"},
                    )
                    if r.status_code in (401, 403):
                        r2 = client.get(
                            "https://api.moonshot.cn/v1/models",
                            headers={"Authorization": f"Bearer {key}"},
                        )
                        if r2.status_code in (401, 403):
                            return {"ok": False, "message": "Moonshot Kimi rejected this API key."}
                else:
                    endpoint_map = {
                        "groq": "https://api.groq.com/openai/v1/models",
                        "openai": "https://api.openai.com/v1/models",
                        "xai": "https://api.x.ai/v1/models",
                        "nvidia": "https://integrate.api.nvidia.com/v1/models",
                        "custom": f"{(custom_base_url or 'https://openrouter.ai/api/v1').rstrip('/')}/models",
                    }
                    check_url = endpoint_map.get(provider, endpoint_map["groq"])
                    r = client.get(check_url, headers={"Authorization": f"Bearer {key}"})
                    if r.status_code in (401, 403):
                        return {"ok": False, "message": f"{badge} rejected this API key — check for typos."}
        except httpx.RequestError:
            verified_msg = f"✓ {badge} key saved & activated (offline right now)."

        updates: dict[str, Any] = {
            "engine_mode": "cloud",
            "cloud_provider": provider,
            "api_keys": {provider: key},
        }
        if custom_base_url.strip():
            updates["custom_base_url"] = custom_base_url.strip()
        if custom_model is not None:
            updates["custom_model"] = custom_model.strip()

        self.config_mgr.update(**updates)
        signal_named_event(RELOAD_EVENT_NAME)
        return {"ok": True, "message": verified_msg}

    def save_state(self, payload: dict[str, Any]) -> bool:
        allowed = {
            "engine_mode",
            "cloud_provider",
            "api_keys",
            "custom_base_url",
            "custom_model",
            "local_model_size",
            "language",
            "hotkey_mode",
            "remove_fillers",
            "smart_formatting",
            "style_mode",
            "custom_dictionary",
            "restore_clipboard",
            "sound_cues",
            "ui_sounds",
            "input_device",
        }
        clean = {k: payload[k] for k in allowed if k in payload}
        self.config_mgr.update(**clean)
        signal_named_event(RELOAD_EVENT_NAME)
        return True


def _apply_dark_dwm_and_icon(title: str) -> None:
    """Apply dark Windows 11 DWM titlebar (#0A0B0E) and custom winflow.ico to the WebView2 HWND."""
    time.sleep(0.18)
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return
    try:
        dark = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), 4)
        # DWMWA_CAPTION_COLOR = 35 (COLORREF 0x00BBGGRR -> #0A0B0E = 0x000E0B0A)
        cap_color = ctypes.c_int(0x000E0B0A)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cap_color), 4)
    except Exception:
        pass

    ico = PROJECT_ROOT / "assets" / "winflow.ico"
    if ico.exists():
        try:
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x0010
            WM_SETICON = 0x0080
            hicon_big = user32.LoadImageW(None, str(ico), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            hicon_sm = user32.LoadImageW(None, str(ico), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
            if hicon_big:
                user32.SendMessageW(hwnd, WM_SETICON, 1, hicon_big)
            if hicon_sm:
                user32.SendMessageW(hwnd, WM_SETICON, 0, hicon_sm)
        except Exception:
            pass


def run_standalone_settings_window(
    initial_tab: int = 0,
    config_path: Path = CONFIG_PATH,
) -> int:
    import webview

    mutex = kernel32.CreateMutexW(None, False, SETTINGS_MUTEX_NAME)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        signal_named_event(FOCUS_SETTINGS_EVENT_NAME)
        return 0

    focus_event = kernel32.CreateEventW(None, False, False, FOCUS_SETTINGS_EVENT_NAME)
    cm = ConfigManager(config_path)
    api = _SettingsJsApi(cm, initial_tab=initial_tab)
    win_title = "WinFlow"

    window = webview.create_window(
        win_title,
        html=MINIMALIST_HTML,
        js_api=api,
        width=580,
        height=710,
        min_size=(480, 460),
        background_color="#0A0B0E",
    )

    alive = [True]

    def _focus_watcher() -> None:
        while alive[0] and focus_event:
            if kernel32.WaitForSingleObject(focus_event, 400) == 0:
                try:
                    window.restore()
                    window.show()
                    hwnd = user32.FindWindowW(None, win_title)
                    if hwnd:
                        user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass

    def _on_shown() -> None:
        threading.Thread(target=_apply_dark_dwm_and_icon, args=(win_title,), daemon=True).start()
        threading.Thread(target=_focus_watcher, daemon=True).start()

    try:
        webview.start(_on_shown, gui="edgechromium")
    finally:
        alive[0] = False
        if focus_event:
            kernel32.CloseHandle(focus_event)
        if mutex:
            kernel32.CloseHandle(mutex)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tab", type=int, default=0)
    parser.add_argument("--config", type=str, default=str(CONFIG_PATH))
    args = parser.parse_args()
    raise SystemExit(run_standalone_settings_window(initial_tab=args.tab, config_path=Path(args.config)))


if __name__ == "__main__":
    main()
