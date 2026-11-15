"""
Native Win32 zero-latency acoustic cues via winsound (winmm.dll).
Completely decoupled from PortAudio/sounddevice so playing UI or dictation sounds
never contends with microphone capture or audio device enumeration.
"""

from __future__ import annotations

import io
import wave
import winsound
from functools import lru_cache

import numpy as np

SR = 44100


def _encode_wav(samples: np.ndarray) -> bytes:
    pcm16 = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


@lru_cache(maxsize=16)
def _build_cue_wav(kind: str) -> bytes:
    if kind == "start":
        # Crisp upward harmonic bloom (D5 -> A5, 68ms)
        n = int(SR * 0.068)
        t = np.linspace(0.0, 0.068, n, endpoint=False)
        env = np.exp(-t * 36.0) * np.minimum(1.0, t / 0.004)
        sig = (0.13 * np.sin(2 * np.pi * 587.33 * t) + 0.10 * np.sin(2 * np.pi * 880.0 * t)) * env
        return _encode_wav(sig)

    if kind == "stop":
        # Soft tactile completion pip (F#5, 52ms)
        n = int(SR * 0.052)
        t = np.linspace(0.0, 0.052, n, endpoint=False)
        env = np.exp(-t * 48.0) * np.minimum(1.0, t / 0.003)
        sig = 0.12 * np.sin(2 * np.pi * 739.99 * t) * env
        return _encode_wav(sig)

    if kind == "cancel":
        # Low muted double-tick (330 Hz -> 247 Hz, 75ms)
        n = int(SR * 0.075)
        t = np.linspace(0.0, 0.075, n, endpoint=False)
        freq = np.where(t < 0.035, 330.0, 246.94)
        env = np.exp(-t * 34.0) * np.minimum(1.0, t / 0.003)
        sig = 0.11 * np.sin(2 * np.pi * freq * t) * env
        return _encode_wav(sig)

    if kind == "apply":
        # Warm fifth chord confirmation (F5 + C6, 95ms)
        n = int(SR * 0.095)
        t = np.linspace(0.0, 0.095, n, endpoint=False)
        env = np.exp(-t * 28.0) * np.minimum(1.0, t / 0.004)
        sig = (0.11 * np.sin(2 * np.pi * 698.46 * t) + 0.09 * np.sin(2 * np.pi * 1046.50 * t)) * env
        return _encode_wav(sig)

    # Default "click": 16ms precision polycarbonate switch tap
    n = int(SR * 0.018)
    t = np.linspace(0.0, 0.018, n, endpoint=False)
    phase = 2 * np.pi * (1200.0 * t - 24000.0 * (t**2))
    env = np.exp(-t * 220.0)
    sig = 0.14 * np.sin(phase) * env
    return _encode_wav(sig)


def play_sound(kind: str = "click", enabled: bool = True) -> None:
    if not enabled:
        return
    try:
        wav_bytes = _build_cue_wav(kind)
        winsound.PlaySound(
            wav_bytes,
            winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
        )
    except Exception:
        pass
