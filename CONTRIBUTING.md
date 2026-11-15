# Contributing to WinFlow

Contributions of bug fixes, latency optimizations, and UI improvements are welcome.

## Development Setup

```powershell
git clone https://github.com/piyushmaurya6/winflow.git
cd winflow
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r pyproject.toml pyinstaller
```

## Run & Test

```powershell
# Unit tests
.\.venv\Scripts\python.exe tests\unit_test.py

# Launch from source
.\.venv\Scripts\pythonw.exe run_winflow.py

# Build native WinFlow.exe + standalone WinFlow-Setup.exe
.\.venv\Scripts\python.exe scripts\build_release.py
```

## Guidelines
- **Never commit secrets:** Keep API keys strictly inside local `config.json` (excluded via `.gitignore`).
- **Win32 x64 safety:** Verify 64-bit `ULONG_PTR` / `LRESULT` / `WPARAM` / `LPARAM` widths (`sizeof(INPUT) == 40` on x64).
- **Zero focus stealing:** Overlay changes must preserve `WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST`.
