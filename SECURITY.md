# Security Policy

## Local Data & API Key Privacy

- **Local-only credential storage:** API keys entered in WinFlow are stored strictly on your device (`%LOCALAPPDATA%\WinFlow\config.json`) and are never transmitted anywhere except the provider you explicitly select. WinFlow collects **zero telemetry or analytics**.
- **Offline Mode:** Audio is processed locally in memory via `faster-whisper` (`local_files_only=True`) and discarded immediately after transcription.
- **Native Binaries (No Script Downloaders):** WinFlow distributes a compiled 64-bit standalone installer (`WinFlow-Setup.exe`) and executable (`WinFlow.exe`) with zero `.bat` or `.vbs` downloader scripts.

## Reporting a Vulnerability

Please report security issues privately via GitHub Security Advisories:
https://github.com/piyushmaurya6/winflow/security/advisories/new
