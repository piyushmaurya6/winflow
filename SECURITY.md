# Security Policy

## Local Data & API Key Privacy

- **Local-only credential storage:** Any Groq or Google Gemini API keys entered in WinFlow Settings are stored strictly on your own machine in `config.json` (excluded from Git via `.gitignore`). WinFlow has **zero telemetry, analytics, or third-party tracking servers**.
- **100% Offline Mode:** When set to **Offline Local Whisper**, audio is captured in-memory, processed locally via `faster-whisper` (`local_files_only=True`), and discarded immediately after transcription — zero bytes leave your machine.
- **Cloud Mode:** When set to **Free Cloud API**, trimmed 16 kHz mono WAV audio is transmitted directly over HTTPS (`TLS 1.2+`) only to the provider you selected (`api.groq.com` or `generativelanguage.googleapis.com`).

## Reporting a Vulnerability

If you discover a security vulnerability in WinFlow, please open a private security advisory on GitHub at:
https://github.com/piyushmaurya6/winflow/security/advisories/new
