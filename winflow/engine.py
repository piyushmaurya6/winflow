from __future__ import annotations

import base64
import ctypes
import gc
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import httpx
import numpy as np

from .cleaner import (
    build_llm_cleanup_system_prompt,
    clean_text_locally,
    is_hallucination,
)
from .config import MODELS_DIR, AppConfig


def _is_cuda12_runtime_available() -> bool:
    """Verify CUDA 12 cuBLAS DLL is actually loadable BEFORE creating a CTranslate2 CUDA model."""
    try:
        ctypes.WinDLL("cublas64_12.dll")
        return True
    except OSError:
        return False


@dataclass
class TranscriptionResult:
    raw_text: str
    final_text: str
    mode_used: str
    latency_ms: int


class SpeechEngine:
    """
    Dual-mode speech engine:
      1. Cloud Mode: Groq (whisper-large-v3-turbo + qwen/qwen3.8-27b 240ms AI cleanup)
                     or Google Gemini (gemini-2.5-flash)
      2. Offline Local Mode: faster-whisper (int8 CPU / verified CUDA) + local disfluency cleaner
    """

    def __init__(self, on_status: Callable[[str], None] | None = None) -> None:
        self.on_status = on_status
        self._local_lock = threading.Lock()
        self._whisper_model = None
        self._loaded_model_key: tuple[str, str] | None = None
        self._active_device_label: str = "CPU"
        self._cuda_ok = _is_cuda12_runtime_available()
        # Persistent HTTP pool keeps TLS connection to api.groq.com warm (<300ms round-trips)
        self._http = httpx.Client(
            timeout=httpx.Timeout(10.0, connect=4.0),
            limits=httpx.Limits(max_keepalive_connections=4, max_connections=8),
        )

    def _emit_status(self, msg: str) -> None:
        if self.on_status:
            try:
                self.on_status(msg)
            except Exception:
                pass

    def warm_cloud_connection_async(self, cfg: AppConfig) -> None:
        """Pre-warm DNS + TLS connection to Groq so the very first Win+H press is sub-second."""
        if cfg.engine_mode != "cloud" or cfg.cloud_provider != "groq" or not cfg.groq_api_key.strip():
            return

        def _warm() -> None:
            try:
                self._http.get(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {cfg.groq_api_key.strip()}"},
                    timeout=4.0,
                )
            except Exception:
                pass

        threading.Thread(target=_warm, name="WinFlow-GroqWarmup", daemon=True).start()

    def unload_local_model(self) -> None:
        """Free RAM when switching from Local Mode to Cloud Mode."""
        with self._local_lock:
            if self._whisper_model is not None:
                self._whisper_model = None
                self._loaded_model_key = None
                gc.collect()

    def preload_local_model_async(self, cfg: AppConfig) -> None:
        def _bg() -> None:
            try:
                self._get_or_load_local_model(cfg, silent=True)
            except Exception:
                pass

        threading.Thread(target=_bg, name="WinFlow-ModelPreload", daemon=True).start()

    def _resolve_device(self, requested: str) -> str:
        if requested in ("auto", "cuda") and self._cuda_ok:
            return "cuda"
        return "cpu"

    def _get_or_load_local_model(self, cfg: AppConfig, silent: bool = False):
        from faster_whisper import WhisperModel

        actual_device = self._resolve_device(cfg.local_device)
        target_key = (cfg.local_model_size, actual_device)

        with self._local_lock:
            if self._whisper_model is not None and self._loaded_model_key == target_key:
                return self._whisper_model

            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            if not silent:
                self._emit_status(f"Loading {cfg.local_model_size}…")

            cpu_threads = max(2, min(6, (os.cpu_count() or 4) - 1))
            cached_dir = MODELS_DIR / f"models--Systran--faster-whisper-{cfg.local_model_size}"
            has_local_cache = cached_dir.exists() and any(cached_dir.rglob("model.bin"))

            kwargs: dict = {
                "device": actual_device,
                "compute_type": "int8_float16" if actual_device == "cuda" else "int8",
                "download_root": str(MODELS_DIR),
            }
            if actual_device == "cpu":
                kwargs["cpu_threads"] = cpu_threads

            model = None
            if has_local_cache:
                try:
                    model = WhisperModel(cfg.local_model_size, local_files_only=True, **kwargs)
                except Exception:
                    model = None
            if model is None:
                model = WhisperModel(cfg.local_model_size, local_files_only=False, **kwargs)

            self._whisper_model = model
            self._loaded_model_key = target_key
            self._active_device_label = "GPU" if actual_device == "cuda" else "CPU"
            return model

    def process_audio(
        self,
        samples: np.ndarray,
        wav_bytes: bytes,
        cfg: AppConfig,
    ) -> TranscriptionResult:
        t0 = time.perf_counter()

        if cfg.engine_mode == "cloud":
            if cfg.cloud_provider == "gemini":
                if not cfg.gemini_api_key.strip():
                    raise RuntimeError("Missing Gemini API key in Settings.")
                raw_text, final_text = self._transcribe_gemini(wav_bytes, cfg)
                mode_used = "Cloud (Gemini)"
            else:
                if not cfg.groq_api_key.strip():
                    raise RuntimeError("Missing Groq API key in Settings.")
                raw_text, final_text = self._transcribe_groq(wav_bytes, cfg)
                mode_used = "Cloud (Groq)"
        else:
            raw_text, final_text = self._transcribe_local(samples, cfg)
            mode_used = f"Local ({cfg.local_model_size}/{self._active_device_label})"

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return TranscriptionResult(
            raw_text=raw_text,
            final_text=final_text,
            mode_used=mode_used,
            latency_ms=elapsed_ms,
        )

    def _transcribe_local(self, samples: np.ndarray, cfg: AppConfig) -> tuple[str, str]:
        model = self._get_or_load_local_model(cfg, silent=False)
        self._emit_status("Transcribing…")

        lang = None if cfg.language == "auto" else cfg.language
        initial_prompt = cfg.custom_dictionary.strip() or None

        with self._local_lock:
            segments, _ = model.transcribe(
                samples,
                language=lang,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300, "speech_pad_ms": 180},
                condition_on_previous_text=False,
                initial_prompt=initial_prompt,
            )
            raw_text = " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()

        if is_hallucination(raw_text):
            return "", ""

        final_text = clean_text_locally(
            raw_text,
            remove_fillers=cfg.remove_fillers,
            smart_formatting=cfg.smart_formatting,
            style_mode=cfg.style_mode,
            custom_dictionary=cfg.custom_dictionary,
        )
        return raw_text, final_text

    def _transcribe_groq(self, wav_bytes: bytes, cfg: AppConfig) -> tuple[str, str]:
        self._emit_status("Transcribing…")
        headers = {"Authorization": f"Bearer {cfg.groq_api_key.strip()}"}
        data: dict[str, str] = {
            "model": "whisper-large-v3-turbo",
            "response_format": "json",
            "temperature": "0",
        }
        if cfg.language and cfg.language != "auto":
            data["language"] = cfg.language
        if cfg.custom_dictionary.strip():
            data["prompt"] = cfg.custom_dictionary.strip()

        resp = self._http.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers=headers,
            data=data,
            files={"file": ("speech.wav", wav_bytes, "audio/wav")},
            timeout=9.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Groq STT error ({resp.status_code})")

        raw_text = (resp.json().get("text") or "").strip()
        if is_hallucination(raw_text):
            return "", ""

        if cfg.style_mode == "raw" or (not cfg.remove_fillers and not cfg.smart_formatting):
            return raw_text, raw_text

        self._emit_status("Polishing…")
        sys_prompt = build_llm_cleanup_system_prompt(
            remove_fillers=cfg.remove_fillers,
            smart_formatting=cfg.smart_formatting,
            style_mode=cfg.style_mode,
            custom_dictionary=cfg.custom_dictionary,
        )

        # Primary ultra-fast Groq model (~220ms): qwen/qwen3.8-27b (non-thinking),
        # with openai/gpt-oss-20b as backup, and instant local cleaner as final fallback.
        for model_id, extra_body in (
            ("qwen/qwen3.8-27b", {"reasoning_effort": "none"}),
            ("openai/gpt-oss-20b", {"reasoning_effort": "low"}),
        ):
            try:
                chat_resp = self._http.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": model_id,
                        "temperature": 0.1,
                        "max_tokens": 800,
                        "messages": [
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": f"<TRANSCRIPT>\n{raw_text}\n</TRANSCRIPT>"},
                        ],
                        **extra_body,
                    },
                    timeout=4.0,
                )
                if chat_resp.status_code == 200:
                    choices = chat_resp.json().get("choices") or []
                    if choices:
                        cleaned = (choices[0].get("message", {}).get("content") or "").strip()
                        cleaned = (
                            cleaned.removeprefix("<TRANSCRIPT>")
                            .removesuffix("</TRANSCRIPT>")
                            .strip()
                        )
                        if cleaned:
                            return raw_text, cleaned
            except Exception:
                continue

        return raw_text, clean_text_locally(
            raw_text,
            remove_fillers=cfg.remove_fillers,
            smart_formatting=cfg.smart_formatting,
            style_mode=cfg.style_mode,
            custom_dictionary=cfg.custom_dictionary,
        )

    def _transcribe_gemini(self, wav_bytes: bytes, cfg: AppConfig) -> tuple[str, str]:
        self._emit_status("Gemini Transcribing…")
        b64_audio = base64.b64encode(wav_bytes).decode("ascii")
        if cfg.style_mode == "raw":
            instruction = (
                "Transcribe this speech audio verbatim. If there is no intelligible speech, output NOTHING. "
                "Output ONLY the transcript with no quotes or extra commentary."
            )
        else:
            instruction = (
                build_llm_cleanup_system_prompt(
                    remove_fillers=cfg.remove_fillers,
                    smart_formatting=cfg.smart_formatting,
                    style_mode=cfg.style_mode,
                    custom_dictionary=cfg.custom_dictionary,
                )
                + "\nIf the audio is silent or contains no intelligible words, return an empty string."
            )

        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash:generateContent?key={cfg.gemini_api_key.strip()}"
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": instruction},
                        {"inline_data": {"mime_type": "audio/wav", "data": b64_audio}},
                    ]
                }
            ],
            "generationConfig": {"temperature": 0.1},
        }
        resp = self._http.post(url, json=payload, timeout=10.0)
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini error ({resp.status_code})")

        body = resp.json()
        candidates = body.get("candidates") or []
        if not candidates:
            return "", ""
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        if is_hallucination(text):
            return "", ""
        return text, text

    def close(self) -> None:
        self.unload_local_model()
        try:
            self._http.close()
        except Exception:
            pass
