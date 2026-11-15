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

PROVIDER_SPECS: dict[str, dict[str, str]] = {
    "groq": {
        "label": "Groq",
        "badge": "Groq Turbo",
        "placeholder": "Paste gsk_... Groq API key",
        "portal_label": "Get free Groq key ↗",
        "portal_url": "https://console.groq.com/keys",
        "hint": "Free Groq key runs Whisper Large v3 Turbo + Qwen 3.8 (~0.8s).",
    },
    "gemini": {
        "label": "Gemini",
        "badge": "Gemini Flash",
        "placeholder": "Paste AIza... / AQ... Gemini API key",
        "portal_label": "Get free Gemini key ↗",
        "portal_url": "https://aistudio.google.com/apikey",
        "hint": "Google Gemini 3.5 Flash Lite single-pass audio & Hinglish cleanup.",
    },
    "openai": {
        "label": "OpenAI GPT",
        "badge": "OpenAI GPT",
        "placeholder": "Paste sk-... OpenAI API key",
        "portal_label": "Get OpenAI key ↗",
        "portal_url": "https://platform.openai.com/api-keys",
        "hint": "OpenAI GPT-4o Mini Transcribe + GPT-4o Mini cleanup.",
    },
    "anthropic": {
        "label": "Claude",
        "badge": "Anthropic Claude",
        "placeholder": "Paste sk-ant-... Anthropic Claude key",
        "portal_label": "Get Claude key ↗",
        "portal_url": "https://console.anthropic.com/settings/keys",
        "hint": "Anthropic Claude Haiku / Sonnet cleanup & Hinglish Romanizer.",
    },
    "xai": {
        "label": "xAI Grok",
        "badge": "xAI Grok",
        "placeholder": "Paste xai-... Grok API key",
        "portal_label": "Get xAI Grok key ↗",
        "portal_url": "https://console.x.ai/",
        "hint": "Elon Musk's xAI Grok fast cleanup & Hinglish Romanizer.",
    },
    "nvidia": {
        "label": "NVIDIA",
        "badge": "NVIDIA NIM",
        "placeholder": "Paste nvapi-... NVIDIA NIM key",
        "portal_label": "Get free NVIDIA key ↗",
        "portal_url": "https://build.nvidia.com/explore/discover",
        "hint": "NVIDIA NIM (Llama / Nemotron / Qwen) cleanup & Hinglish Romanizer.",
    },
    "kimi": {
        "label": "Kimi",
        "badge": "Moonshot Kimi",
        "placeholder": "Paste sk-... Moonshot Kimi key",
        "portal_label": "Get Kimi key ↗",
        "portal_url": "https://platform.moonshot.ai/console/api-keys",
        "hint": "Moonshot AI Kimi K2 cleanup & Hinglish Romanizer (.ai & .cn supported).",
    },
    "custom": {
        "label": "Custom +",
        "badge": "Custom API",
        "placeholder": "Paste sk-... OpenRouter / DeepSeek / Custom key",
        "portal_label": "OpenRouter keys ↗",
        "portal_url": "https://openrouter.ai/keys",
        "hint": "Any OpenAI-compatible endpoint (OpenRouter, DeepSeek, Together, Cerebras, Ollama).",
    },
}

SUPPORTED_PROVIDERS = tuple(PROVIDER_SPECS.keys())


@dataclass
class AppConfig:
    # "local" (100% offline faster-whisper) or "cloud" (Groq / Gemini / GPT / Claude / Grok / NVIDIA / Kimi / Custom)
    engine_mode: str = "local"
    cloud_provider: str = "groq"

    # Legacy fields kept synced for backward compatibility
    groq_api_key: str = ""
    gemini_api_key: str = ""

    # Multi-provider API key vault: {provider_id: api_key}
    api_keys: dict[str, str] = field(default_factory=dict)
    custom_base_url: str = "https://openrouter.ai/api/v1"
    custom_model: str = ""

    # Local Whisper & Language configuration
    local_model_size: str = "base"  # tiny, base, small, distil-large-v3
    local_device: str = "auto"      # auto, cuda, cpu
    # "en" (English), "hinglish" (Hindi in English letters like 'Apna'), "hi" (Devanagari), "auto"
    language: str = "en"

    # Hotkey behavior: "hybrid" (tap toggles, hold push-to-talk), "toggle", "hold"
    hotkey_mode: str = "hybrid"
    hold_threshold_ms: int = 350

    # AI & Cleanup features
    remove_fillers: bool = True
    smart_formatting: bool = True
    style_mode: str = "clean"
    custom_dictionary: str = "Wispr Flow, WinFlow, GitHub, Windows"

    # Output & UX
    restore_clipboard: bool = True
    sound_cues: bool = True
    ui_sounds: bool = True
    input_device: int | None = None
    history: list[dict[str, Any]] = field(default_factory=list)

    def sync_keys(self) -> None:
        """Keep legacy groq_api_key / gemini_api_key and api_keys dict strictly in sync."""
        if not isinstance(self.api_keys, dict):
            self.api_keys = {}
        if self.groq_api_key and not self.api_keys.get("groq"):
            self.api_keys["groq"] = self.groq_api_key.strip()
        if self.gemini_api_key and not self.api_keys.get("gemini"):
            self.api_keys["gemini"] = self.gemini_api_key.strip()
        if "groq" in self.api_keys:
            self.groq_api_key = (self.api_keys.get("groq") or "").strip()
        if "gemini" in self.api_keys:
            self.gemini_api_key = (self.api_keys.get("gemini") or "").strip()

    def get_api_key(self, provider: str | None = None) -> str:
        prov = provider or self.cloud_provider
        self.sync_keys()
        return (self.api_keys.get(prov) or "").strip()

    def set_api_key(self, provider: str, key: str) -> None:
        clean_key = (key or "").strip()
        if not isinstance(self.api_keys, dict):
            self.api_keys = {}
        self.api_keys[provider] = clean_key
        if provider == "groq":
            self.groq_api_key = clean_key
        elif provider == "gemini":
            self.gemini_api_key = clean_key


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
                    self.data.sync_keys()
                except Exception:
                    self.data = AppConfig()
            else:
                env_map = [
                    ("groq", os.environ.get("GROQ_API_KEY", "")),
                    (
                        "gemini",
                        os.environ.get("GEMINI_API_KEY", "")
                        or os.environ.get("GOOGLE_API_KEY", ""),
                    ),
                    ("openai", os.environ.get("OPENAI_API_KEY", "")),
                    ("anthropic", os.environ.get("ANTHROPIC_API_KEY", "")),
                    ("xai", os.environ.get("XAI_API_KEY", "")),
                    ("nvidia", os.environ.get("NVIDIA_API_KEY", "")),
                    ("kimi", os.environ.get("MOONSHOT_API_KEY", "")),
                ]
                for prov, val in env_map:
                    if val.strip():
                        self.data.set_api_key(prov, val.strip())
                        if self.data.engine_mode == "local":
                            self.data.cloud_provider = prov
                            self.data.engine_mode = "cloud"
                self.save()
            return self.data

    def save(self) -> None:
        with self._lock:
            self.data.sync_keys()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(asdict(self.data), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_path.replace(self.path)

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            # Ensure api_keys merges cleanly if passed partially, while keeping legacy fields synced
            incoming_keys = kwargs.pop("api_keys", None)
            if isinstance(incoming_keys, dict):
                merged = dict(self.data.api_keys or {})
                for p, k in incoming_keys.items():
                    if p in SUPPORTED_PROVIDERS:
                        merged[p] = (k or "").strip()
                self.data.api_keys = merged
                if "groq" in merged:
                    self.data.groq_api_key = merged["groq"]
                if "gemini" in merged:
                    self.data.gemini_api_key = merged["gemini"]

            for k, v in kwargs.items():
                if hasattr(self.data, k):
                    setattr(self.data, k, v)
                    if k == "groq_api_key":
                        self.data.api_keys["groq"] = (v or "").strip()
                    elif k == "gemini_api_key":
                        self.data.api_keys["gemini"] = (v or "").strip()
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
            return bool(self.data.get_api_key())


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
