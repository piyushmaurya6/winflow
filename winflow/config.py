from __future__ import annotations

import json
import os
import threading
import time
import winreg
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
MODELS_DIR = PROJECT_ROOT / "models"
STARTUP_VBS_PATH = PROJECT_ROOT / "Start-WinFlow.vbs"
RUN_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_REG_NAME = "WinFlow"


@dataclass
class AppConfig:
    # "local" (100% offline faster-whisper) or "cloud" (Groq / Gemini free tier)
    engine_mode: str = "local"
    # "groq" or "gemini"
    cloud_provider: str = "groq"
    groq_api_key: str = ""
    gemini_api_key: str = ""

    # Local Whisper configuration
    local_model_size: str = "base"  # tiny, base, small, distil-large-v3
    local_device: str = "auto"      # auto, cuda, cpu
    language: str = "en"            # "en" or "auto"

    # Hotkey behavior: "hybrid" (tap toggles, hold push-to-talk), "toggle", "hold"
    hotkey_mode: str = "hybrid"
    hold_threshold_ms: int = 350

    # AI & Cleanup features
    remove_fillers: bool = True
    smart_formatting: bool = True
    # "clean" (keep exact voice, strip fillers/stutters & fix punctuation),
    # "polished" (concise & polished rewrite),
    # "raw" (verbatim transcription)
    style_mode: str = "clean"
    custom_dictionary: str = "Wispr Flow, WinFlow, GitHub, Windows"

    # Output & UX
    restore_clipboard: bool = True
    sound_cues: bool = True
    input_device: int | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


class ConfigManager:
    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self.data = AppConfig()
        self.load()

    def load(self) -> AppConfig:
        with self._lock:
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    valid_keys = {f for f in AppConfig.__dataclass_fields__}
                    filtered = {k: v for k, v in raw.items() if k in valid_keys}
                    self.data = AppConfig(**filtered)
                except Exception:
                    self.data = AppConfig()
            else:
                # Seed from environment variables if present on first run
                env_groq = os.environ.get("GROQ_API_KEY", "").strip()
                env_gemini = (
                    os.environ.get("GEMINI_API_KEY", "").strip()
                    or os.environ.get("GOOGLE_API_KEY", "").strip()
                )
                if env_groq:
                    self.data.groq_api_key = env_groq
                    self.data.cloud_provider = "groq"
                    self.data.engine_mode = "cloud"
                elif env_gemini:
                    self.data.gemini_api_key = env_gemini
                    self.data.cloud_provider = "gemini"
                    self.data.engine_mode = "cloud"
                self.save()
            return self.data

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(asdict(self.data), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_path.replace(self.path)

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self.data, k):
                    setattr(self.data, k, v)
            self.save()

    def add_history(
        self,
        raw_text: str,
        cleaned_text: str,
        mode_label: str,
        latency_ms: int,
    ) -> None:
        with self._lock:
            entry = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "raw": raw_text,
                "cleaned": cleaned_text,
                "mode": mode_label,
                "latency_ms": latency_ms,
            }
            self.data.history.insert(0, entry)
            self.data.history = self.data.history[:30]
            self.save()

    def has_active_cloud_key(self) -> bool:
        with self._lock:
            if self.data.cloud_provider == "gemini":
                return bool(self.data.gemini_api_key.strip())
            return bool(self.data.groq_api_key.strip())


def is_startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_REG_PATH, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, RUN_REG_NAME)
            return bool(val)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_startup_enabled(enable: bool) -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_REG_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enable:
                cmd = f'wscript.exe "{STARTUP_VBS_PATH}"'
                winreg.SetValueEx(key, RUN_REG_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(key, RUN_REG_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
