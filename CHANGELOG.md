# Changelog

All notable changes to **WinFlow** are documented in this file.

## [1.4.1] - 2026-11-22

### Fixed
- **Bluetooth Earbuds & Hotplug Microphone Auto-Detection (`winflow/audio.py`)**:
  - Fixed `Mic error` on Desktop PCs without a built-in microphone when connecting Bluetooth earbuds/headsets after WinFlow has already started: WinFlow now detects live Windows `waveIn` audio endpoint changes in <1 ms and automatically re-initializes PortAudio (`sd._terminate()` + `sd._initialize()`) without requiring an app restart.
  - Added a 4.5-second **`Connecting mic…`** Bluetooth HFP handshake grace window if you press `Alt + X` while your Bluetooth earbuds are still finishing Windows' 4–5 second connection refresh.
  - Added name-based microphone persistence (`input_device_name`), automatic fallback to any connected mic, automatic sample-rate adaptation (`8 kHz` / `44.1 kHz` / `48 kHz` → `16 kHz`), and live `Microphone` dropdown refresh in Settings (filtering out dormant `WDM-KS` ghost drivers).

## [1.4.0] - 2026-11-16

### Added
- **Automatic GitHub Update Checker & Downloader (`winflow/updater.py`)**: Every time WinFlow starts or opens, it checks `piyushmaurya6/winflow` on GitHub for a newer release in the background. When an update is available, WinFlow automatically downloads and SHA-256-verifies `WinFlow-Setup.exe` in the background and prompts you to update and restart in one click.
- **Single-Bar `--auto-update` Installer Mode (`installer/WinFlowSetup.cs`)**: Clicking **Yes** on the update prompt runs `WinFlow-Setup.exe --auto-update` with a single smooth `Updating…` progress bar, replaces `WinFlow.exe` while keeping all saved API keys and offline models intact, and automatically relaunches WinFlow.
- **Manual `Check Update` Controls**: Added one-click `Check Update` in the app footer, `Check for Updates` in the System Tray menu, and an `Auto-Check Updates` toggle in Settings.

## [1.3.1] - 2026-11-16

### Fixed
- **Floating Speaking Overlay Visibility (`winflow/overlay.py`)**:
  - Added explicit `SetWindowPos(HWND_TOPMOST, ..., SWP_NOACTIVATE | SWP_SHOWWINDOW)` Z-order promotion when showing and animating the pill so Windows 11 taskbars, maximized browsers/IDEs, and startup-order topmost bands never bury the overlay after `SW_HIDE`.
  - Enabled native **Per-Monitor V2 DPI Awareness** (`PerMonitorV2` manifest + `SetProcessDpiAwarenessContext`) and active-monitor work-area tracking (`MonitorFromWindow` / `MonitorFromPoint` + `MONITORINFO.rcWork` + `GetDpiForMonitor`) so the pill always renders crisply above the taskbar on whichever monitor you are actively using.
  - Added automatic HWND self-healing (`PostThreadMessageW` fallback + `UpdateLayeredWindow` verification) to recover seamlessly after lock screen, display sleep, or monitor hotplug.

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
