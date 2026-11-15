# Contributing to WinFlow

Thank you for your interest in improving **WinFlow**! Contributions of bug fixes, latency optimizations, language presets, and UI polish are very welcome.

## Development Setup

1. **Clone the repository:**
   ```powershell
   git clone https://github.com/piyushmaurya6/winflow.git
   cd winflow
   ```

2. **Create an isolated Python 3.12 virtual environment with [uv](https://docs.astral.sh/uv/):**
   ```powershell
   uv venv --python 3.12 .venv
   uv pip install --python .venv\Scripts\python.exe -r pyproject.toml
   ```

3. **Run the automated verification suite:**
   ```powershell
   .\.venv\Scripts\python.exe tests\self_test.py
   ```

4. **Launch WinFlow locally:**
   ```powershell
   wscript.exe Start-WinFlow.vbs
   ```

## Guidelines
- **Never commit secrets:** Keep API keys strictly inside your local `config.json` (which is `.gitignore`d). Update `config.example.json` only when adding new configuration fields.
- **Win32 x64 safety:** When modifying `ctypes` structures in `winflow/hotkey.py`, `winflow/injector.py`, or `winflow/overlay.py`, verify 64-bit `ULONG_PTR` / `LRESULT` / `WPARAM` / `LPARAM` widths and ensure `sizeof(INPUT) == 40` on 64-bit Windows.
- **Zero focus stealing:** Any overlay UI changes must preserve `WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST` so the user's active typing cursor never loses focus.
