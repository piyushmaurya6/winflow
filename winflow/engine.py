from __future__ import annotations

import base64
import ctypes
import gc
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import httpx
import numpy as np

from .cleaner import (
    VOICE_COMMANDS,
    build_llm_cleanup_system_prompt,
    build_whisper_prompt,
    clean_text_locally,
    contains_devanagari,
    is_hallucination,
    transliterate_devanagari_to_hinglish,
)
from .config import MODELS_DIR, PROVIDER_SPECS, AppConfig


def _is_cuda12_runtime_available() -> bool:
    """Verify CUDA 12 cuBLAS DLL is actually loadable BEFORE creating a CTranslate2 CUDA model."""
    try:
        ctypes.WinDLL("cublas64_12.dll")
        return True
    except OSError:
        return False


OPENAI_COMPAT_PROVIDER_CONFIGS: dict[str, dict[str, Any]] = {
    "groq": {
        "base_urls": ["https://api.groq.com/openai/v1"],
        "models": [
            ("qwen/qwen3.8-27b", {"reasoning_effort": "none"}),
            ("openai/gpt-oss-20b", {"reasoning_effort": "low"}),
        ],
    },
    "openai": {
        "base_urls": ["https://api.openai.com/v1"],
        "models": [
            ("gpt-4o-mini", {}),
            ("gpt-4.1-mini", {}),
            ("gpt-4o", {}),
        ],
    },
    "xai": {
        "base_urls": ["https://api.x.ai/v1"],
        "models": [
            ("grok-3-mini", {}),
            ("grok-4-fast", {}),
            ("grok-3-fast", {}),
            ("grok-3", {}),
        ],
    },
    "nvidia": {
        "base_urls": ["https://integrate.api.nvidia.com/v1"],
        "models": [
            ("meta/llama-3.3-70b-instruct", {}),
            ("qwen/qwen2.5-72b-instruct", {}),
            ("nvidia/llama-3.1-nemotron-70b-instruct", {}),
        ],
    },
    "kimi": {
        "base_urls": ["https://api.moonshot.ai/v1", "https://api.moonshot.cn/v1"],
        "models": [
            ("kimi-k2-0711-preview", {}),
            ("kimi-latest", {}),
            ("moonshot-v1-8k", {}),
        ],
    },
}


@dataclass
class TranscriptionResult:
    raw_text: str
    final_text: str
    mode_used: str
    latency_ms: int


class SpeechEngine:
    """
    Multi-provider AI Voice Dictation & Hinglish Engine:
      - Native Audio + LLM Cloud: Groq, Google Gemini, OpenAI GPT
      - Hybrid STT + LLM Cloud: Anthropic Claude, xAI Grok, NVIDIA NIM, Moonshot Kimi, Custom OpenAI-compatible
      - 100% Offline Local Mode: faster-whisper (int8) + deterministic disfluency & Hinglish Romanizer
    """

    def __init__(self, on_status: Callable[[str], None] | None = None) -> None:
        self.on_status = on_status
        self._local_lock = threading.Lock()
        self._whisper_model = None
        self._loaded_model_key: tuple[str, str] | None = None
        self._active_device_label: str = "CPU"
        self._cuda_ok = _is_cuda12_runtime_available()
        self._discovered_model_cache: dict[str, tuple[str, str]] = {}
        self._http = httpx.Client(
            timeout=httpx.Timeout(10.0, connect=4.0),
            limits=httpx.Limits(max_keepalive_connections=6, max_connections=12),
        )

    def _emit_status(self, msg: str) -> None:
        if self.on_status:
            try:
                self.on_status(msg)
            except Exception:
                pass

    def needs_local_stt(self, cfg: AppConfig) -> bool:
        """Return True if local faster-whisper is needed for raw speech capture."""
        if cfg.engine_mode != "cloud":
            return True
        if cfg.cloud_provider in ("groq", "gemini", "openai"):
            return False
        # Hybrid LLM providers use Cloud Whisper if a Groq or OpenAI key is also in the vault
        if cfg.get_api_key("groq") or cfg.get_api_key("openai"):
            return False
        return True

    def warm_cloud_connection_async(self, cfg: AppConfig) -> None:
        """Pre-warm DNS + TLS connection to active cloud endpoint."""
        if cfg.engine_mode != "cloud":
            return
        prov = cfg.cloud_provider
        key = cfg.get_api_key(prov)
        if not key:
            return

        def _warm() -> None:
            try:
                if prov == "groq" or cfg.get_api_key("groq"):
                    gk = cfg.get_api_key("groq")
                    if gk:
                        self._http.get(
                            "https://api.groq.com/openai/v1/models",
                            headers={"Authorization": f"Bearer {gk}"},
                            timeout=4.0,
                        )
                elif prov == "gemini":
                    self._http.get(
                        "https://generativelanguage.googleapis.com/v1beta/models",
                        headers={"x-goog-api-key": key},
                        timeout=4.0,
                    )
                elif prov in OPENAI_COMPAT_PROVIDER_CONFIGS:
                    base = OPENAI_COMPAT_PROVIDER_CONFIGS[prov]["base_urls"][0]
                    self._http.get(
                        f"{base}/models",
                        headers={"Authorization": f"Bearer {key}"},
                        timeout=4.0,
                    )
            except Exception:
                pass

        threading.Thread(target=_warm, name="WinFlow-CloudWarmup", daemon=True).start()

    def unload_local_model(self) -> None:
        """Free RAM when local faster-whisper is not needed."""
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

    @staticmethod
    def _whisper_lang_param(language: str) -> str | None:
        if language in ("auto", "hinglish", ""):
            return None
        return language

    def _postprocess_output(self, text: str, cfg: AppConfig) -> str:
        out = (
            text.removeprefix("<TRANSCRIPT>")
            .removesuffix("</TRANSCRIPT>")
            .strip()
        )
        if cfg.smart_formatting and cfg.style_mode != "raw":
            for pattern, replacement in VOICE_COMMANDS:
                out = pattern.sub(replacement, out)
        if cfg.language == "hinglish" and contains_devanagari(out):
            out = transliterate_devanagari_to_hinglish(out)
        return out.strip()

    def process_audio(
        self,
        samples: np.ndarray,
        wav_bytes: bytes,
        cfg: AppConfig,
    ) -> TranscriptionResult:
        t0 = time.perf_counter()

        if cfg.engine_mode == "cloud":
            prov = cfg.cloud_provider
            active_key = cfg.get_api_key(prov)
            prov_label = PROVIDER_SPECS.get(prov, {}).get("label", prov.upper())
            if not active_key:
                raise RuntimeError(f"Missing {prov_label} API key in Settings.")

            if prov == "gemini":
                raw_text, final_text = self._transcribe_gemini(wav_bytes, cfg, active_key)
                mode_used = "Cloud (Gemini)"
            elif prov == "groq":
                raw_text = self._stt_groq_whisper(wav_bytes, cfg, active_key)
                final_text = self._polish_with_provider(raw_text, cfg, "groq", active_key)
                mode_used = "Cloud (Groq)"
            elif prov == "openai":
                raw_text = self._stt_openai_whisper(wav_bytes, cfg, active_key)
                final_text = self._polish_with_provider(raw_text, cfg, "openai", active_key)
                mode_used = "Cloud (OpenAI)"
            else:
                # Hybrid Cloud Mode: Claude, xAI Grok, NVIDIA NIM, Moonshot Kimi, Custom
                raw_text = self._stt_hybrid_raw(samples, wav_bytes, cfg)
                final_text = self._polish_with_provider(raw_text, cfg, prov, active_key)
                mode_used = f"Cloud ({prov_label})"
        else:
            raw_text, final_text = self._transcribe_local(samples, cfg)
            mode_used = f"Local ({cfg.local_model_size}/{self._active_device_label})"

        if cfg.language == "hinglish" and final_text:
            mode_used = f"{mode_used} • Hinglish"

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return TranscriptionResult(
            raw_text=raw_text,
            final_text=final_text,
            mode_used=mode_used,
            latency_ms=elapsed_ms,
        )

    # ------------------------------------------------------------------
    # Raw Speech-to-Text Stage (Local / Groq / OpenAI / Hybrid)
    # ------------------------------------------------------------------

    def _transcribe_local_raw(self, samples: np.ndarray, cfg: AppConfig) -> str:
        model = self._get_or_load_local_model(cfg, silent=False)
        self._emit_status("Transcribing…")

        lang = self._whisper_lang_param(cfg.language)
        initial_prompt = build_whisper_prompt(cfg.language, cfg.custom_dictionary)

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

        return "" if is_hallucination(raw_text) else raw_text

    def _transcribe_local(self, samples: np.ndarray, cfg: AppConfig) -> tuple[str, str]:
        raw_text = self._transcribe_local_raw(samples, cfg)
        if not raw_text:
            return "", ""
        final_text = clean_text_locally(
            raw_text,
            remove_fillers=cfg.remove_fillers,
            smart_formatting=cfg.smart_formatting,
            style_mode=cfg.style_mode,
            custom_dictionary=cfg.custom_dictionary,
            language=cfg.language,
        )
        return raw_text, final_text

    def _stt_groq_whisper(self, wav_bytes: bytes, cfg: AppConfig, api_key: str) -> str:
        self._emit_status("Transcribing…")
        headers = {"Authorization": f"Bearer {api_key}"}
        data: dict[str, str] = {
            "model": "whisper-large-v3-turbo",
            "response_format": "json",
            "temperature": "0",
        }
        lang = self._whisper_lang_param(cfg.language)
        if lang:
            data["language"] = lang
        prompt = build_whisper_prompt(cfg.language, cfg.custom_dictionary)
        if prompt:
            data["prompt"] = prompt

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
        return "" if is_hallucination(raw_text) else raw_text

    def _stt_openai_whisper(self, wav_bytes: bytes, cfg: AppConfig, api_key: str) -> str:
        self._emit_status("Transcribing…")
        headers = {"Authorization": f"Bearer {api_key}"}
        lang = self._whisper_lang_param(cfg.language)
        prompt = build_whisper_prompt(cfg.language, cfg.custom_dictionary)

        last_status = 0
        for stt_model in ("gpt-4o-mini-transcribe", "whisper-1"):
            data: dict[str, str] = {
                "model": stt_model,
                "response_format": "json",
            }
            if lang:
                data["language"] = lang
            if prompt:
                data["prompt"] = prompt
            resp = self._http.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers=headers,
                data=data,
                files={"file": ("speech.wav", wav_bytes, "audio/wav")},
                timeout=9.5,
            )
            last_status = resp.status_code
            if resp.status_code == 200:
                raw_text = (resp.json().get("text") or "").strip()
                return "" if is_hallucination(raw_text) else raw_text
            if resp.status_code in (401, 403):
                raise RuntimeError("Invalid OpenAI API key — check Settings")
        raise RuntimeError(f"OpenAI STT error ({last_status})")

    def _stt_hybrid_raw(self, samples: np.ndarray, wav_bytes: bytes, cfg: AppConfig) -> str:
        """Capture raw transcript for LLM-only providers (Claude, Grok, NVIDIA, Kimi, Custom)."""
        groq_key = cfg.get_api_key("groq")
        if groq_key:
            try:
                return self._stt_groq_whisper(wav_bytes, cfg, groq_key)
            except Exception:
                pass
        openai_key = cfg.get_api_key("openai")
        if openai_key:
            try:
                return self._stt_openai_whisper(wav_bytes, cfg, openai_key)
            except Exception:
                pass
        return self._transcribe_local_raw(samples, cfg)

    # ------------------------------------------------------------------
    # LLM Cleanup & Hinglish Romanization Stage (All Providers)
    # ------------------------------------------------------------------

    def _polish_with_provider(
        self, raw_text: str, cfg: AppConfig, provider: str, api_key: str
    ) -> str:
        if not raw_text:
            return ""

        # Only skip LLM if raw mode AND not Hinglish (Hinglish always converts to Roman Latin script!)
        if (
            cfg.language != "hinglish"
            and (cfg.style_mode == "raw" or (not cfg.remove_fillers and not cfg.smart_formatting))
        ):
            return raw_text

        self._emit_status("Polishing…")
        sys_prompt = build_llm_cleanup_system_prompt(
            remove_fillers=cfg.remove_fillers,
            smart_formatting=cfg.smart_formatting,
            style_mode=cfg.style_mode,
            custom_dictionary=cfg.custom_dictionary,
            language=cfg.language,
        )

        cleaned: str | None = None
        if provider == "anthropic":
            cleaned = self._chat_anthropic(raw_text, sys_prompt, api_key, cfg.custom_model)
        elif provider == "custom":
            base_url = (cfg.custom_base_url or "https://openrouter.ai/api/v1").rstrip("/")
            candidates = [(cfg.custom_model.strip(), {})] if cfg.custom_model.strip() else [
                ("openai/gpt-4o-mini", {}),
                ("deepseek-chat", {}),
            ]
            cleaned = self._chat_openai_compatible(
                raw_text, sys_prompt, api_key, [base_url], candidates, cache_key=f"custom:{base_url}"
            )
        elif provider in OPENAI_COMPAT_PROVIDER_CONFIGS:
            spec = OPENAI_COMPAT_PROVIDER_CONFIGS[provider]
            candidates = list(spec["models"])
            if cfg.custom_model.strip() and provider not in ("groq",):
                candidates.insert(0, (cfg.custom_model.strip(), {}))
            cleaned = self._chat_openai_compatible(
                raw_text,
                sys_prompt,
                api_key,
                spec["base_urls"],
                candidates,
                cache_key=provider,
            )

        if cleaned:
            return self._postprocess_output(cleaned, cfg)

        return clean_text_locally(
            raw_text,
            remove_fillers=cfg.remove_fillers,
            smart_formatting=cfg.smart_formatting,
            style_mode=cfg.style_mode,
            custom_dictionary=cfg.custom_dictionary,
            language=cfg.language,
        )

    def _discover_chat_model(self, base_url: str, headers: dict[str, str]) -> str | None:
        try:
            r = self._http.get(f"{base_url}/models", headers=headers, timeout=4.0)
            if r.status_code != 200:
                return None
            items = r.json().get("data") or []
            ids = [
                str(m.get("id", ""))
                for m in items
                if m.get("id")
                and not any(
                    skip in str(m.get("id", "")).lower()
                    for skip in ("whisper", "embed", "tts", "dall-e", "image", "moderation", "guard", "rerank")
                )
            ]
            # Prefer fast mini/flash/instruct models if present
            for kw in ("mini", "flash", "turbo", "fast", "haiku", "kimi", "grok", "instruct"):
                for mid in ids:
                    if kw in mid.lower():
                        return mid
            return ids[0] if ids else None
        except Exception:
            return None

    def _chat_openai_compatible(
        self,
        raw_text: str,
        sys_prompt: str,
        api_key: str,
        base_urls: list[str],
        candidates: list[tuple[str, dict[str, Any]]],
        cache_key: str,
    ) -> str | None:
        headers = {"Authorization": f"Bearer {api_key}"}

        # Prepend previously auto-discovered working model if cached
        cached = self._discovered_model_cache.get(cache_key)
        if cached:
            base_urls = [cached[0]] + [u for u in base_urls if u != cached[0]]
            candidates = [(cached[1], {})] + [c for c in candidates if c[0] != cached[1]]

        for base_url in base_urls:
            auth_failed = False
            for model_id, extra_body in candidates:
                if not model_id:
                    continue
                try:
                    resp = self._http.post(
                        f"{base_url}/chat/completions",
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
                        timeout=6.5,
                    )
                    if resp.status_code == 200:
                        choices = resp.json().get("choices") or []
                        if choices:
                            txt = (choices[0].get("message", {}).get("content") or "").strip()
                            if txt:
                                self._discovered_model_cache[cache_key] = (base_url, model_id)
                                return txt
                    elif resp.status_code in (401, 403):
                        auth_failed = True
                        break
                except Exception:
                    continue

            if auth_failed:
                # Try next base_url (e.g. Kimi .ai -> .cn) before giving up
                continue

            # Dynamic model fallback if static model IDs were retired (404/400)
            discovered = self._discover_chat_model(base_url, headers)
            if discovered:
                try:
                    resp = self._http.post(
                        f"{base_url}/chat/completions",
                        headers=headers,
                        json={
                            "model": discovered,
                            "temperature": 0.1,
                            "max_tokens": 800,
                            "messages": [
                                {"role": "system", "content": sys_prompt},
                                {"role": "user", "content": f"<TRANSCRIPT>\n{raw_text}\n</TRANSCRIPT>"},
                            ],
                        },
                        timeout=6.5,
                    )
                    if resp.status_code == 200:
                        choices = resp.json().get("choices") or []
                        if choices:
                            txt = (choices[0].get("message", {}).get("content") or "").strip()
                            if txt:
                                self._discovered_model_cache[cache_key] = (base_url, discovered)
                                return txt
                except Exception:
                    pass

        return None

    def _chat_anthropic(
        self, raw_text: str, sys_prompt: str, api_key: str, custom_model: str = ""
    ) -> str | None:
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        models = [
            "claude-haiku-4-5",
            "claude-3-5-haiku-latest",
            "claude-sonnet-4-5",
            "claude-3-7-sonnet-latest",
        ]
        if custom_model.strip():
            models.insert(0, custom_model.strip())
        cached = self._discovered_model_cache.get("anthropic")
        if cached:
            models.insert(0, cached[1])

        for model_id in models:
            try:
                resp = self._http.post(
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json={
                        "model": model_id,
                        "max_tokens": 800,
                        "temperature": 0.1,
                        "system": sys_prompt,
                        "messages": [
                            {"role": "user", "content": f"<TRANSCRIPT>\n{raw_text}\n</TRANSCRIPT>"}
                        ],
                    },
                    timeout=7.0,
                )
                if resp.status_code == 200:
                    blocks = resp.json().get("content") or []
                    txt = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
                    if txt:
                        self._discovered_model_cache["anthropic"] = ("anthropic", model_id)
                        return txt
                if resp.status_code in (401, 403):
                    raise RuntimeError("Invalid Anthropic Claude API key — check Settings")
            except RuntimeError:
                raise
            except Exception:
                continue

        # Dynamic fallback via Anthropic /v1/models
        try:
            mr = self._http.get("https://api.anthropic.com/v1/models", headers=headers, timeout=4.0)
            if mr.status_code == 200:
                mdata = mr.json().get("data") or []
                mids = [str(m.get("id", "")) for m in mdata if m.get("id")]
                pick = next((m for m in mids if "haiku" in m.lower()), mids[0] if mids else None)
                if pick:
                    resp = self._http.post(
                        "https://api.anthropic.com/v1/messages",
                        headers=headers,
                        json={
                            "model": pick,
                            "max_tokens": 800,
                            "temperature": 0.1,
                            "system": sys_prompt,
                            "messages": [
                                {"role": "user", "content": f"<TRANSCRIPT>\n{raw_text}\n</TRANSCRIPT>"}
                            ],
                        },
                        timeout=7.0,
                    )
                    if resp.status_code == 200:
                        blocks = resp.json().get("content") or []
                        txt = "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
                        if txt:
                            self._discovered_model_cache["anthropic"] = ("anthropic", pick)
                            return txt
        except Exception:
            pass
        return None

    def _transcribe_gemini(
        self, wav_bytes: bytes, cfg: AppConfig, api_key: str
    ) -> tuple[str, str]:
        self._emit_status("Gemini Transcribing…")
        b64_audio = base64.b64encode(wav_bytes).decode("ascii")
        if cfg.style_mode == "raw" and cfg.language != "hinglish":
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
                    language=cfg.language,
                )
                + "\nIf the audio is silent or contains no intelligible words, return an empty string."
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
        headers = {"x-goog-api-key": api_key}
        last_status = 0
        resp = None
        for model_id in ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash"):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent"
            r = self._http.post(url, headers=headers, json=payload, timeout=10.0)
            last_status = r.status_code
            if r.status_code == 200:
                resp = r
                break
            if r.status_code in (401, 403):
                raise RuntimeError("Invalid Gemini API key — check Settings")

        if resp is None:
            raise RuntimeError(f"Gemini error ({last_status})")

        body = resp.json()
        candidates = body.get("candidates") or []
        if not candidates:
            return "", ""
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts).strip()
        if is_hallucination(text):
            return "", ""
        text = self._postprocess_output(text, cfg)
        return text, text

    def close(self) -> None:
        self.unload_local_model()
        try:
            self._http.close()
        except Exception:
            pass
