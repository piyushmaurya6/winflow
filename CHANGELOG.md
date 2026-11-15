# Changelog

All notable changes to **WinFlow** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
