# Changelog

All notable changes to **WinFlow** are documented in this file.

## [1.3.0] - 2026-11-15

### Added
- **Standalone Native `.exe` Installer (`WinFlow-Setup.exe`)**: Replaced all `.bat` and `.vbs` launcher/downloader scripts with a self-contained 64-bit native Windows installer (`WinFlow-Setup.exe`) licensed by **Piyush Maurya**, featuring a clean license view, a single smooth installation progress bar (with zero file/component clutter), Desktop & Start Menu shortcuts, and a native uninstaller (`Uninstall-WinFlow.exe`).
- **Compiled Standalone `WinFlow.exe`**: Packaged the full WinFlow runtime into a native 64-bit `WinFlow.exe` executable (`--noupx`) requiring zero Python, `uv`, or PowerShell scripts on target PCs.

### Changed
- **Simplified Native App UI**: Redesigned the Settings interface into a compact, uncluttered desktop app (`Engine` | `Settings` | `History`) with simple tactile buttons and switches, removing all long descriptions and placeholder examples.

### Removed
- Removed `WinFlow-QuickInstaller.bat`, `Install-And-Run.bat`, `Start-WinFlow.bat`, `Start-WinFlow.vbs`, and `scripts/install.ps1` so antivirus heuristics never flag script downloaders on other PCs.

## [1.2.0] - 2026-11-15

### Added
- **Hinglish Mode (`"Apna"`)**: Romanized Hindi-in-English-letters output across Cloud and Offline modes.
- **8-Provider Cloud Vault**: Added Groq, Gemini, GPT, Claude, Grok, NVIDIA, Kimi, and Custom OpenAI-compatible API support.

## [1.1.0] - 2026-11-15

### Added
- **Edge WebView2 UI + Tactile Button Sounds**: Isolated STA-process UI with inline API key verification and Web Audio micro-acoustics.

## [1.0.0] - 2026-11-15

### Added
- Initial Windows 10/11 release with `Alt + X` hook (`Win + H` untouched), Cloud & Offline Whisper STT, disfluency cleanup, and Win32 ARGB floating pill.
