from __future__ import annotations

import io
import threading
import wave
from typing import Any

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
CHANNELS = 1


def list_input_devices() -> list[tuple[int | None, str]]:
    """Return [(None, 'System Default Microphone'), (index, name), ...]"""
    result: list[tuple[int | None, str]] = [(None, "System Default Microphone")]
    try:
        devices = sd.query_devices()
        seen_names: set[str] = set()
        for idx, dev in enumerate(devices):
            if dev.get("max_input_channels", 0) > 0:
                name = str(dev.get("name", f"Mic {idx}")).strip()
                # Skip duplicate host-API entries for cleaner dropdown UX
                if name and name not in seen_names:
                    seen_names.add(name)
                    result.append((idx, name))
    except Exception:
        pass
    return result


class AudioRecorder:
    """Low-latency 16kHz mono microphone capture with live RMS metering."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._current_level = 0.0

    @property
    def current_level(self) -> float:
        return self._current_level

    def start(self, device: int | None = None) -> None:
        with self._lock:
            self._stop_unlocked()
            self._chunks = []
            self._current_level = 0.0
            self._recording = True
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                blocksize=1024,
                device=device,
                callback=self._audio_callback,
            )
            self._stream.start()

    def _audio_callback(
        self, indata: np.ndarray, frames: int, time_info: Any, status: sd.CallbackFlags
    ) -> None:
        if not self._recording:
            return
        chunk = indata[:, 0].copy()
        self._chunks.append(chunk)
        rms = float(np.sqrt(np.mean(np.square(chunk))) + 1e-8)
        # Smooth perceptual level scaled to 0.0 .. 1.0
        scaled = min(1.0, rms * 8.5)
        self._current_level = 0.55 * self._current_level + 0.45 * scaled

    def stop(self) -> tuple[np.ndarray, bytes, float]:
        """Stop recording and return (float32_samples, wav_bytes, duration_seconds)."""
        with self._lock:
            self._recording = False
            chunks = self._chunks
            self._chunks = []
            self._current_level = 0.0
            self._stop_unlocked()

        if not chunks:
            return np.zeros(0, dtype=np.float32), b"", 0.0

        samples = np.concatenate(chunks).astype(np.float32)
        duration = len(samples) / float(SAMPLE_RATE)

        # Trim leading/trailing dead silence (>0.2s pad kept) so Cloud uploads are tiny & fast
        abs_s = np.abs(samples)
        active_idx = np.where(abs_s > 0.008)[0]
        if len(active_idx) == 0:
            return np.zeros(0, dtype=np.float32), b"", 0.0
        pad_samples = int(0.2 * SAMPLE_RATE)
        start_i = max(0, int(active_idx[0]) - pad_samples)
        end_i = min(len(samples), int(active_idx[-1]) + pad_samples)
        samples = samples[start_i:end_i]
        duration = len(samples) / float(SAMPLE_RATE)

        # Peak-normalize softly if mic gain is very quiet (helps Whisper accuracy)
        peak = float(np.max(np.abs(samples))) if len(samples) > 0 else 0.0
        if 0.004 < peak < 0.25:
            samples = np.clip(samples * (0.45 / peak), -1.0, 1.0)

        pcm16 = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(pcm16.tobytes())

        return samples, buf.getvalue(), duration

    def cancel(self) -> None:
        with self._lock:
            self._recording = False
            self._chunks = []
            self._current_level = 0.0
            self._stop_unlocked()

    def _stop_unlocked(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None


def play_cue(kind: str, enabled: bool = True) -> None:
    """Play a gentle, non-blocking soft sine tone for start/stop/cancel cues."""
    if not enabled:
        return

    def _worker() -> None:
        try:
            sr = 24000
            if kind == "start":
                t = np.linspace(0, 0.075, int(sr * 0.075), endpoint=False)
                tone = 0.09 * np.sin(2 * np.pi * 587.33 * t) + 0.07 * np.sin(2 * np.pi * 880.0 * t)
            elif kind == "stop":
                t = np.linspace(0, 0.07, int(sr * 0.07), endpoint=False)
                tone = 0.08 * np.sin(2 * np.pi * 740.0 * t)
            else:
                t = np.linspace(0, 0.08, int(sr * 0.08), endpoint=False)
                tone = 0.07 * np.sin(2 * np.pi * 330.0 * t)

            env = np.hanning(len(tone))
            sd.play((tone * env).astype(np.float32), samplerate=sr, blocking=False)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()
