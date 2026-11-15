# Changelog

All notable changes to **WinFlow** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-11-15

### Added
- **Minimalist Edge WebView2 Control Center (`winflow/settings_ui.py`)**: Replaced the legacy Tkinter settings window with a hardware-accelerated minimalist UI running in its own isolated STA process — eliminating Win32 OLE clipboard freezes when pasting API keys (`Ctrl+V` / `Paste`) and preventing bottom-bar clipping across all Windows high-DPI scaling levels (`100%–200%`).
- **Inline `Apply Key` + Live Verification**: Added an inline **`Paste`**, **`Show/Hide`**, and **`Apply Key`** button directly beside the API key input that verifies Groq/Gemini keys asynchronously and signals the background `Alt + X` daemon immediately via Win32 Named Events (`Local\WinFlowReloadConfigEvent`).
- **Tactile Button Acoustics (`winflow/sounds.py` + Web Audio)**: Every button, segmented tab, pill, and toggle switch now produces crisp, synthesized tactile micro-sounds (`press`, `tab`, `toggleOn`, `toggleOff`, `apply`, `error`) with a one-click **`Sound On / Muted`** header toggle, while background dictation cues run via native `winmm.dll` (`winsound`) with zero PortAudio contention.
- **First-Run & Re-Launch Polish**: Automatically opens Settings on first install and focuses Settings whenever the user re-launches the Desktop shortcut while WinFlow is already running in the system tray.

### Fixed
- Fixed Settings window freeze when pasting API keys or enumerating slow Bluetooth/WDM-KS microphones.
- Fixed missing/clipped bottom Save button on `125%–175%` DPI laptop screens via a flexbox sticky **`Apply & Save`** footer.
- Updated Google Gemini Cloud engine to `gemini-3.5-flash-lite` (with automatic fallback) for 2026 API keys where `gemini-2.5-flash` was retired (`404`).

## [1.0.0] - 2026-11-15

### Added
- **Global `Alt + X` Low-Level Hook (`WH_KEYBOARD_LL`)**: Smart Hybrid mode supporting quick-tap hands-free toggle, hold-to-speak push-to-talk, `Esc` cancellation, and `vkE8` menu-bar suppression while leaving native Windows Voice Typing (`Win + H`) completely untouched.
- **Dual Switchable Speech Engines**:
  - **Free Cloud API Mode**: Groq (`whisper-large-v3-turbo` + `qwen/qwen3.8-27b` sub-second AI cleanup) and Google Gemini (`gemini-2.5-flash` single-pass audio + cleanup).
  - **100% Offline Local Mode**: `faster-whisper` (`tiny`, `base`, `small`, `distil-large-v3`) with Silero VAD, `local_files_only` zero-network startup, automatic CUDA 12 runtime verification, and automatic RAM unload when switching to Cloud mode.
- **Wispr-Style AI Disfluency & Formatting Cleanup**: Removes spoken hesitations (*"um"*, *"uh"*, *"like"*, *"you know"*), collapses word/phrase stutters (*"can Can"* → *"Can"*), applies custom vocabulary spellings, and converts spoken formatting commands (*"new line"*, *"new paragraph"*).
- **Non-Intrusive Floating Pill (`UpdateLayeredWindow`)**: Per-pixel 32-bit ARGB Win32 overlay with live WASAPI RMS waveform bars that never steals focus from the active typing window.
- **Dark Windows 11 Settings & History UI**: Multi-tab configuration panel, microphone selector, custom dictionary, startup registry toggle, Pause toggle, and 1-click copy dictation history.
- **One-Click Windows Installer**: PowerShell bootstrapper (`scripts/install.ps1`) and `Install-And-Run.bat`.
