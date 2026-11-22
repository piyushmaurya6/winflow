from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import io
import logging
import threading
import time
import wave
from typing import Any, Callable

import numpy as np
import sounddevice as sd

log = logging.getLogger("winflow.audio")

SAMPLE_RATE = 16000
CHANNELS = 1

_winmm = ctypes.WinDLL("winmm", use_last_error=True)


class _WAVEINCAPSW(ctypes.Structure):
    _fields_ = [
        ("wMid", wintypes.WORD),
        ("wPid", wintypes.WORD),
        ("vDriverVersion", wintypes.UINT),
        ("szPname", wintypes.WCHAR * 32),
        ("dwFormats", wintypes.DWORD),
        ("wChannels", wintypes.WORD),
        ("wReserved1", wintypes.WORD),
    ]


_winmm.waveInGetNumDevs.argtypes = []
_winmm.waveInGetNumDevs.restype = wintypes.UINT
_winmm.waveInGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(_WAVEINCAPSW), wintypes.UINT]
_winmm.waveInGetDevCapsW.restype = wintypes.UINT
_winmm.waveInMessage.argtypes = [
    ctypes.c_size_t,
    wintypes.UINT,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]
_winmm.waveInMessage.restype = wintypes.UINT

_SIZE_T_MASK = (1 << (ctypes.sizeof(ctypes.c_size_t) * 8)) - 1
_WAVE_MAPPER = (-1) & _SIZE_T_MASK
_DRVM_MAPPER_PREFERRED_GET = 0x2015

_PA_LOCK = threading.RLock()
_LAST_PA_FINGERPRINT: tuple[int, int, tuple[str, ...]] | None = None
_ACTIVE_STREAMS = 0


def _get_live_wavein_fingerprint() -> tuple[int, int, tuple[str, ...]]:
    """
    Query Windows winmm directly (~0.5ms, never cached by PortAudio) for the live count,
    preferred default device ID, and names of currently active audio input endpoints.
    """
    try:
        count = int(_winmm.waveInGetNumDevs())
        names: list[str] = []
        caps = _WAVEINCAPSW()
        cb = ctypes.sizeof(_WAVEINCAPSW)
        for i in range(count):
            if _winmm.waveInGetDevCapsW(i, ctypes.byref(caps), cb) == 0:
                names.append(caps.szPname.strip())
        pref_id = wintypes.DWORD(0xFFFFFFFF)
        flags = wintypes.DWORD(0)
        _winmm.waveInMessage(
            _WAVE_MAPPER,
            _DRVM_MAPPER_PREFERRED_GET,
            ctypes.byref(pref_id),
            ctypes.byref(flags),
        )
        return (count, int(pref_id.value), tuple(names))
    except Exception:
        return (-1, -1, ())


def ensure_portaudio_fresh(force: bool = False) -> bool:
    """
    Re-initialize PortAudio whenever Windows audio input endpoints changed since startup
    (e.g., Bluetooth earbuds connected on a Desktop PC that booted with 0 microphones).
    """
    global _LAST_PA_FINGERPRINT
    with _PA_LOCK:
        if _ACTIVE_STREAMS > 0:
            return False

        live_fp = _get_live_wavein_fingerprint()
        try:
            default_in = sd.default.device[0]
        except Exception:
            default_in = -1

        needs_reinit = (
            force
            or _LAST_PA_FINGERPRINT is None
            or live_fp != _LAST_PA_FINGERPRINT
            or (live_fp[0] > 0 and (default_in is None or int(default_in) < 0))
        )
        if not needs_reinit:
            return False

        prev_fp = _LAST_PA_FINGERPRINT
        try:
            if prev_fp is not None or force or (default_in is not None and int(default_in) < 0):
                sd._terminate()
                sd._initialize()
                log.info("Refreshed PortAudio input devices (live_mics=%s)", live_fp[0])
            _LAST_PA_FINGERPRINT = live_fp
            return True
        except Exception as exc:
            log.warning("PortAudio refresh warning: %s", exc)
            _LAST_PA_FINGERPRINT = live_fp
            return False


# Initialize baseline fingerprint at import time
_LAST_PA_FINGERPRINT = _get_live_wavein_fingerprint()


def _usable_input_devices() -> list[tuple[int, str, str, float, int]]:
    """
    Return [(idx, clean_name, hostapi_name, default_sr, max_ch), ...] excluding dormant WDM-KS
    kernel ghost drivers and generic mapper aliases.
    """
    out: list[tuple[int, str, str, float, int]] = []
    try:
        hostapis = sd.query_hostapis()
        devices = sd.query_devices()
    except Exception:
        return out

    skip_Low = ("microsoft sound mapper", "primary sound capture")
    for idx, dev in enumerate(devices):
        max_ch = int(dev.get("max_input_channels", 0) or 0)
        if max_ch <= 0:
            continue
        api_idx = int(dev.get("hostapi", 0) or 0)
        api_name = str(hostapis[api_idx].get("name", "")) if 0 <= api_idx < len(hostapis) else ""
        if "WDM-KS" in api_name.upper():
            continue
        name = str(dev.get("name", f"Mic {idx}")).strip()
        if not name or any(s in name.lower() for s in skip_Low):
            continue
        sr = float(dev.get("default_samplerate", SAMPLE_RATE) or SAMPLE_RATE)
        out.append((idx, name, api_name, sr, max_ch))
    return out


def list_input_devices() -> list[tuple[int | None, str]]:
    """Return [(None, 'System Default Microphone'), (index, name), ...] refreshed live."""
    ensure_portaudio_fresh(force=False)
    result: list[tuple[int | None, str]] = [(None, "System Default Microphone")]
    usable = _usable_input_devices()

    # Prefer MME first for broad sample-rate compatibility, using full WASAPI/DirectSound names when truncated
    full_names = [name for _, name, api, _, _ in usable if "MME" not in api.upper()]
    seen_keys: set[str] = set()

    # Sort so MME comes first, then WASAPI, then DirectSound
    def _api_rank(item: tuple[int, str, str, float, int]) -> int:
        api_u = item[2].upper()
        if "MME" in api_u:
            return 0
        if "WASAPI" in api_u:
            return 1
        return 2

    for idx, name, _api, _sr, _ch in sorted(usable, key=_api_rank):
        display_name = name
        if len(name) >= 31:
            for fn in full_names:
                if fn.lower().startswith(name.lower()[:28]):
                    display_name = fn
                    break
        dedup_key = display_name.lower()[:28]
        if dedup_key not in seen_keys:
            seen_keys.add(dedup_key)
            result.append((idx, display_name))
    return result


def resolve_device_name(device_index: int | None) -> str:
    if device_index is None:
        return ""
    for idx, name in list_input_devices():
        if idx == device_index:
            return name
    return ""


def _resample_to_16k(samples: np.ndarray, src_rate: int) -> np.ndarray:
    if src_rate == SAMPLE_RATE or len(samples) == 0:
        return samples
    out_len = max(1, int(round(len(samples) * (SAMPLE_RATE / float(src_rate)))))
    x_old = np.linspace(0.0, 1.0, num=len(samples), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=out_len, endpoint=False)
    return np.interp(x_new, x_old, samples).astype(np.float32)


class AudioRecorder:
    """
    Hotplug-resilient 16kHz microphone capture with live RMS metering.
    Automatically detects Bluetooth earbuds / USB headsets connected after startup,
    waits briefly if Bluetooth HFP is mid-handshake, and adapts sample rates seamlessly.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._stream_rate: int = SAMPLE_RATE
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._cancelled = False
        self._current_level = 0.0

    @property
    def current_level(self) -> float:
        return self._current_level

    def _build_candidate_devices(
        self,
        preferred_index: int | None,
        preferred_name: str = "",
    ) -> list[int | None]:
        candidates: list[int | None] = []
        usable = _usable_input_devices()

        # 1. Match saved microphone by name first (survives Bluetooth index shifts)
        clean_pref = (preferred_name or "").strip().lower()
        if clean_pref:
            prefix = clean_pref[:26]
            for idx, name, _api, _sr, _ch in usable:
                if name.lower() == clean_pref or name.lower().startswith(prefix) or clean_pref.startswith(name.lower()[:26]):
                    if idx not in candidates:
                        candidates.append(idx)

        # 2. Match saved index if still a valid usable input device
        if preferred_index is not None:
            if any(idx == preferred_index for idx, *_ in usable) and preferred_index not in candidates:
                candidates.append(preferred_index)

        # 3. System default input (None) + MME Sound Mapper (0 if input)
        candidates.append(None)
        try:
            devs = sd.query_devices()
            for idx, d in enumerate(devs):
                if int(d.get("max_input_channels", 0) or 0) > 0 and "sound mapper" in str(d.get("name", "")).lower():
                    if idx not in candidates:
                        candidates.append(idx)
        except Exception:
            pass

        # 4. Any connected usable microphone (critical on Desktop PCs where a newly connected Bluetooth headset isn't Default yet)
        for idx, _name, _api, _sr, _ch in usable:
            if idx not in candidates:
                candidates.append(idx)

        return candidates

    def _try_open_candidate(self, dev: int | None) -> tuple[sd.InputStream, int] | None:
        rates_to_try: list[int] = [SAMPLE_RATE]
        channels_to_try: list[int] = [1]
        try:
            info = sd.query_devices(dev, "input")
            native_sr = int(round(float(info.get("default_samplerate", SAMPLE_RATE) or SAMPLE_RATE)))
            if native_sr > 0 and native_sr not in rates_to_try:
                rates_to_try.append(native_sr)
            max_ch = int(info.get("max_input_channels", 1) or 1)
            if max_ch >= 2:
                channels_to_try.append(2)
        except Exception:
            for fallback_sr in (48000, 44100, 8000):
                if fallback_sr not in rates_to_try:
                    rates_to_try.append(fallback_sr)

        for sr in rates_to_try:
            for ch in channels_to_try:
                stream = None
                try:
                    blocksize = max(256, int(round(sr * 0.064)))
                    stream = sd.InputStream(
                        samplerate=sr,
                        channels=ch,
                        dtype="float32",
                        blocksize=blocksize,
                        device=dev,
                        callback=self._audio_callback,
                    )
                    stream.start()
                    return stream, sr
                except Exception:
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:
                            pass
        return None

    def start(
        self,
        device: int | None = None,
        device_name: str = "",
        on_waiting: Callable[[str], None] | None = None,
        should_abort: Callable[[], bool] | None = None,
        wait_for_bt_s: float = 4.5,
    ) -> None:
        global _ACTIVE_STREAMS
        self._cancelled = False

        # If Desktop PC currently shows 0 microphones (e.g. Bluetooth earbuds just connected & Windows is mid-refresh),
        # poll briefly up to `wait_for_bt_s` so the mic connects seamlessly without throwing an instant error.
        live_fp = _get_live_wavein_fingerprint()
        if live_fp[0] == 0 and wait_for_bt_s > 0:
            if on_waiting:
                on_waiting("Connecting mic…")
            deadline = time.monotonic() + wait_for_bt_s
            while time.monotonic() < deadline and not self._cancelled:
                if should_abort and should_abort():
                    raise RuntimeError("Cancelled")
                time.sleep(0.20)
                live_fp = _get_live_wavein_fingerprint()
                if live_fp[0] > 0:
                    break
            if self._cancelled or (should_abort and should_abort()):
                raise RuntimeError("Cancelled")
            if live_fp[0] == 0:
                raise RuntimeError("No mic connected — connect earphones")

        with self._lock:
            self._stop_unlocked()
            self._chunks = []
            self._current_level = 0.0

            # Refresh PortAudio if Windows audio devices changed since boot/last use
            ensure_portaudio_fresh(force=False)

            opened: tuple[sd.InputStream, int] | None = None
            for attempt in range(2):
                for cand in self._build_candidate_devices(device, device_name):
                    opened = self._try_open_candidate(cand)
                    if opened is not None:
                        break
                if opened is not None:
                    break
                # Force full PortAudio re-enumeration + brief Bluetooth HFP settle pause before retry
                time.sleep(0.25)
                ensure_portaudio_fresh(force=True)

            if opened is None:
                raise RuntimeError("No working microphone found")

            self._stream, self._stream_rate = opened
            self._recording = True
            with _PA_LOCK:
                _ACTIVE_STREAMS += 1

    def _audio_callback(
        self, indata: np.ndarray, frames: int, time_info: Any, status: sd.CallbackFlags
    ) -> None:
        if not self._recording:
            return
        if indata.ndim == 2 and indata.shape[1] > 1:
            chunk = np.mean(indata, axis=1, dtype=np.float32)
        else:
            chunk = indata[:, 0].copy() if indata.ndim == 2 else indata.copy()
        self._chunks.append(chunk)
        rms = float(np.sqrt(np.mean(np.square(chunk))) + 1e-8)
        scaled = min(1.0, rms * 8.5)
        self._current_level = 0.55 * self._current_level + 0.45 * scaled

    def stop(self) -> tuple[np.ndarray, bytes, float]:
        """Stop recording and return (float32_16k_samples, wav_bytes, duration_seconds)."""
        with self._lock:
            self._recording = False
            chunks = self._chunks
            src_rate = self._stream_rate
            self._chunks = []
            self._current_level = 0.0
            self._stop_unlocked()

        if not chunks:
            return np.zeros(0, dtype=np.float32), b"", 0.0

        samples = np.concatenate(chunks).astype(np.float32)
        if src_rate != SAMPLE_RATE:
            samples = _resample_to_16k(samples, src_rate)

        duration = len(samples) / float(SAMPLE_RATE)

        abs_s = np.abs(samples)
        active_idx = np.where(abs_s > 0.008)[0]
        if len(active_idx) == 0:
            return np.zeros(0, dtype=np.float32), b"", 0.0
        pad_samples = int(0.2 * SAMPLE_RATE)
        start_i = max(0, int(active_idx[0]) - pad_samples)
        end_i = min(len(samples), int(active_idx[-1]) + pad_samples)
        samples = samples[start_i:end_i]
        duration = len(samples) / float(SAMPLE_RATE)

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
        self._cancelled = True
        with self._lock:
            self._recording = False
            self._chunks = []
            self._current_level = 0.0
            self._stop_unlocked()

    def _stop_unlocked(self) -> None:
        global _ACTIVE_STREAMS
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
            with _PA_LOCK:
                _ACTIVE_STREAMS = max(0, _ACTIVE_STREAMS - 1)


from .sounds import play_sound


def play_cue(kind: str, enabled: bool = True) -> None:
    """Play a gentle, non-blocking native WinMM tone without touching PortAudio."""
    play_sound(kind, enabled=enabled)
