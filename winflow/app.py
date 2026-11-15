from __future__ import annotations

import concurrent.futures
import ctypes
import logging
import os
import threading

import pystray
from PIL import Image, ImageDraw

from .audio import AudioRecorder, play_cue
from .config import (
    CONFIG_PATH,
    LOG_PATH,
    PROVIDER_SPECS,
    SUPPORTED_PROVIDERS,
    ConfigManager,
    is_startup_enabled,
    set_startup_enabled,
)
from .engine import SpeechEngine
from .hotkey import AltXHotkeyListener
from .injector import paste_text_at_cursor, set_clipboard_text
from .overlay import FloatingPillOverlay
from .settings_ui import SHUTDOWN_EVENT_NAME, SettingsWindowController, kernel32
from .sounds import play_sound

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("winflow.app")

MAX_RECORD_SECONDS = 90.0
HARD_TRANSCRIBE_TIMEOUT_S = 14.0


def _create_tray_icon(mode: str, recording: bool = False, paused: bool = False) -> Image.Image:
    """Render a crisp 64x64 system tray icon reflecting current mode & state."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    if paused:
        bg = (107, 114, 128, 255)
    elif recording:
        bg = (239, 68, 68, 255)
    elif mode == "cloud":
        bg = (79, 124, 255, 255)
    else:
        bg = (34, 197, 132, 255)

    d.rounded_rectangle((4, 4, 60, 60), radius=16, fill=bg)
    bars = [(17, 26, 38), (27, 16, 48), (37, 20, 44), (47, 27, 37)]
    for x, y1, y2 in bars:
        d.rounded_rectangle((x - 3, y1, x + 3, y2), radius=3, fill=(255, 255, 255, 255))
    return img


class WinFlowApp:
    def __init__(self) -> None:
        self._first_run = not CONFIG_PATH.exists()
        self.config_mgr = ConfigManager()
        self.recorder = AudioRecorder()
        self.overlay = FloatingPillOverlay(get_audio_level=lambda: self.recorder.current_level)
        self.engine = SpeechEngine(on_status=self.overlay.show_processing)
        self.settings_ui = SettingsWindowController(
            self.config_mgr, on_config_saved=lambda: self._on_config_updated(from_ui=True)
        )
        self._paused = False
        self.hotkey = AltXHotkeyListener(
            on_start=self._handle_record_start,
            on_stop=self._handle_record_stop,
            on_cancel=self._handle_record_cancel,
            on_mode_changed=self._handle_mode_changed,
            get_hotkey_mode=lambda: self.config_mgr.data.hotkey_mode,
            get_hold_threshold_ms=lambda: self.config_mgr.data.hold_threshold_ms,
            is_paused=lambda: self._paused,
        )
        self.tray: pystray.Icon | None = None
        self._busy_transcribing = threading.Lock()
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="WinFlow-STT"
        )
        self._max_record_timer: threading.Timer | None = None
        self._mutex_handle = None
        self._shutdown_event = None

    def _acquire_single_instance(self) -> bool:
        suffix = os.environ.get("WINFLOW_INSTANCE_ID", "")
        self._mutex_handle = kernel32.CreateMutexW(
            None, False, f"Local\\WinFlowSingleInstanceMutex{suffix}"
        )
        last_err = ctypes.get_last_error()
        ERROR_ALREADY_EXISTS = 183
        return last_err != ERROR_ALREADY_EXISTS

    def _start_shutdown_watcher(self) -> None:
        self._shutdown_event = kernel32.CreateEventW(None, False, False, SHUTDOWN_EVENT_NAME)

        def _watch() -> None:
            while self._shutdown_event:
                if kernel32.WaitForSingleObject(self._shutdown_event, 500) == 0:
                    self.shutdown()
                    break

        threading.Thread(target=_watch, name="WinFlow-ShutdownWatch", daemon=True).start()

    def _cancel_max_timer(self) -> None:
        if self._max_record_timer is not None:
            self._max_record_timer.cancel()
            self._max_record_timer = None

    def _handle_record_start(self, trigger_kind: str) -> None:
        log.info("Alt+X intercepted -> start (%s)", trigger_kind)
        if self._busy_transcribing.locked():
            self.hotkey.reset_state()
            self.overlay.show_toast("Finishing…", duration_s=1.0)
            return

        cfg = self.config_mgr.data
        try:
            self.recorder.start(device=cfg.input_device)
        except Exception as exc:
            self.hotkey.reset_state()
            self.overlay.show_toast(f"Mic error: {exc}", is_error=True, duration_s=2.2)
            return

        self._cancel_max_timer()
        self._max_record_timer = threading.Timer(MAX_RECORD_SECONDS, self._auto_stop_long_recording)
        self._max_record_timer.daemon = True
        self._max_record_timer.start()

        play_cue("start", enabled=cfg.sound_cues)
        if trigger_kind == "holding":
            self.overlay.show_listening("Listening…")
        elif trigger_kind == "toggled":
            self.overlay.show_listening("Listening… (Alt+X)")
        else:
            self.overlay.show_listening("Listening…")
        self._refresh_tray_icon(recording=True)

    def _auto_stop_long_recording(self) -> None:
        if self.hotkey.is_recording:
            self.hotkey.reset_state()
            self._handle_record_stop()

    def _handle_mode_changed(self, new_mode: str) -> None:
        if new_mode == "toggled":
            self.overlay.show_listening("Listening… (Alt+X)")
        elif new_mode == "holding":
            self.overlay.show_listening("Release to paste")

    def _handle_record_cancel(self) -> None:
        self._cancel_max_timer()
        self.recorder.cancel()
        play_cue("cancel", enabled=self.config_mgr.data.sound_cues)
        self.overlay.show_toast("Cancelled", is_error=False, duration_s=0.8)
        self._refresh_tray_icon(recording=False)

    def _handle_record_stop(self) -> None:
        self._cancel_max_timer()
        samples, wav_bytes, duration_s = self.recorder.stop()
        self._refresh_tray_icon(recording=False)
        play_cue("stop", enabled=self.config_mgr.data.sound_cues)

        if duration_s < 0.18 or len(samples) == 0:
            self.overlay.hide()
            return

        threading.Thread(
            target=self._transcribe_and_paste_guarded,
            args=(samples, wav_bytes),
            name="WinFlow-TranscribeGuard",
            daemon=True,
        ).start()

    def _transcribe_and_paste_guarded(self, samples, wav_bytes: bytes) -> None:
        if not self._busy_transcribing.acquire(blocking=False):
            return

        toast_shown = False
        try:
            cfg = self.config_mgr.data
            self.overlay.show_processing("Transcribing…")
            fut = self._executor.submit(self.engine.process_audio, samples, wav_bytes, cfg)
            try:
                res = fut.result(timeout=HARD_TRANSCRIBE_TIMEOUT_S)
            except concurrent.futures.TimeoutError:
                log.error("Transcription timed out after %.1fs", HARD_TRANSCRIBE_TIMEOUT_S)
                self.overlay.show_toast("Timed out", is_error=True, duration_s=1.8)
                toast_shown = True
                return
            except Exception as exc:
                log.exception("Transcription error")
                self.overlay.show_toast(str(exc), is_error=True, duration_s=2.2)
                toast_shown = True
                return

            if not res.final_text:
                self.overlay.show_toast("No speech", is_error=False, duration_s=0.8)
                toast_shown = True
                return

            pasted = paste_text_at_cursor(
                res.final_text, restore_clipboard=cfg.restore_clipboard
            )
            self.config_mgr.add_history(
                raw_text=res.raw_text,
                cleaned_text=res.final_text,
                mode_label=res.mode_used,
                latency_ms=res.latency_ms,
            )
            log.info("Transcribed via %s in %dms", res.mode_used, res.latency_ms)
            if pasted:
                self.overlay.show_toast("Pasted", duration_s=0.75)
            else:
                set_clipboard_text(res.final_text)
                self.overlay.show_toast("Copied", duration_s=1.0)
            toast_shown = True
        finally:
            if not toast_shown:
                self.overlay.hide()
            self._busy_transcribing.release()

    def _toggle_paused(self) -> None:
        play_sound("click", enabled=self.config_mgr.data.ui_sounds)
        self._paused = not self._paused
        if self._paused:
            self._cancel_max_timer()
            self.hotkey.reset_state()
            self.recorder.cancel()
            self.overlay.show_toast("Paused", duration_s=1.2)
        else:
            self.overlay.show_toast("Active (Alt+X)", duration_s=1.2)
        self._refresh_tray_icon(recording=False)
        if self.tray:
            self.tray.update_menu()

    def _switch_engine_mode(self, mode: str) -> None:
        play_sound("click", enabled=self.config_mgr.data.ui_sounds)
        if mode == "cloud" and not self.config_mgr.has_active_cloud_key():
            self.overlay.show_toast("Add API key first", is_error=True, duration_s=1.8)
            self.settings_ui.open_settings(tab_index=0)
            return

        self._paused = False
        self.config_mgr.update(engine_mode=mode)
        self._on_config_updated(from_ui=False)
        prov_badge = PROVIDER_SPECS.get(self.config_mgr.data.cloud_provider, {}).get("badge", "Cloud")
        badge = prov_badge if mode == "cloud" else "Offline"
        self.overlay.show_toast(badge, duration_s=1.1)

    def _switch_cloud_provider(self, prov: str) -> None:
        play_sound("click", enabled=self.config_mgr.data.ui_sounds)
        if not self.config_mgr.data.get_api_key(prov):
            self.config_mgr.update(cloud_provider=prov)
            lbl = PROVIDER_SPECS.get(prov, {}).get("label", prov.upper())
            self.overlay.show_toast(f"Add {lbl} key", is_error=True, duration_s=1.8)
            self.settings_ui.open_settings(tab_index=0)
            return
        self._paused = False
        self.config_mgr.update(engine_mode="cloud", cloud_provider=prov)
        self._on_config_updated(from_ui=False)
        badge = PROVIDER_SPECS.get(prov, {}).get("badge", prov.upper())
        self.overlay.show_toast(badge, duration_s=1.1)

    def _set_language(self, lang: str) -> None:
        play_sound("click", enabled=self.config_mgr.data.ui_sounds)
        self.config_mgr.update(language=lang)
        self._refresh_tray_icon(recording=False)
        if self.tray:
            self.tray.update_menu()
        labels = {
            "en": "English",
            "hinglish": "Hinglish",
            "hi": "Hindi",
            "auto": "Auto",
        }
        self.overlay.show_toast(labels.get(lang, lang), duration_s=1.0)

    def _set_style_mode(self, style_mode: str) -> None:
        play_sound("click", enabled=self.config_mgr.data.ui_sounds)
        self.config_mgr.update(style_mode=style_mode)
        if self.tray:
            self.tray.update_menu()

    def _toggle_fillers(self) -> None:
        play_sound("click", enabled=self.config_mgr.data.ui_sounds)
        cur = self.config_mgr.data.remove_fillers
        self.config_mgr.update(remove_fillers=not cur)
        if self.tray:
            self.tray.update_menu()

    def _toggle_startup(self) -> None:
        play_sound("click", enabled=self.config_mgr.data.ui_sounds)
        set_startup_enabled(not is_startup_enabled())
        if self.tray:
            self.tray.update_menu()

    def _copy_last_dictation(self) -> None:
        play_sound("click", enabled=self.config_mgr.data.ui_sounds)
        hist = self.config_mgr.data.history
        if hist:
            set_clipboard_text(hist[0].get("cleaned", ""))
            self.overlay.show_toast("Copied", duration_s=0.9)
        else:
            self.overlay.show_toast("No history", duration_s=0.9)

    def _on_config_updated(self, from_ui: bool = False) -> None:
        cfg = self.config_mgr.data
        if self.engine.needs_local_stt(cfg):
            self.engine.preload_local_model_async(cfg)
        else:
            self.engine.unload_local_model()
        if cfg.engine_mode == "cloud":
            self.engine.warm_cloud_connection_async(cfg)
        self._refresh_tray_icon(recording=False)
        if self.tray:
            self.tray.update_menu()
        if from_ui and not self.hotkey.is_recording:
            badge = (
                PROVIDER_SPECS.get(cfg.cloud_provider, {}).get("badge", "Cloud")
                if cfg.engine_mode == "cloud"
                else "Offline"
            )
            if cfg.language == "hinglish":
                badge = f"{badge} • Hinglish"
            self.overlay.show_toast(f"Saved • {badge}", duration_s=1.0)

    def _refresh_tray_icon(self, recording: bool = False) -> None:
        if not self.tray:
            return
        cfg = self.config_mgr.data
        mode = cfg.engine_mode
        self.tray.icon = _create_tray_icon(mode=mode, recording=recording, paused=self._paused)
        if self._paused:
            self.tray.title = "WinFlow — Paused"
            return
        provider_desc = (
            PROVIDER_SPECS.get(cfg.cloud_provider, {}).get("badge", cfg.cloud_provider.upper())
            if mode == "cloud"
            else "Offline"
        )
        lang_tag = " • Hinglish" if cfg.language == "hinglish" else ""
        self.tray.title = f"WinFlow — {provider_desc}{lang_tag} (Alt+X)"

    def _make_provider_item(self, prov: str) -> pystray.MenuItem:
        label = PROVIDER_SPECS[prov]["label"]
        return pystray.MenuItem(
            label,
            lambda icon, item: self._switch_cloud_provider(prov),
            checked=lambda item: (
                self.config_mgr.data.engine_mode == "cloud"
                and self.config_mgr.data.cloud_provider == prov
            ),
            radio=True,
        )

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                "Open WinFlow",
                lambda icon, item: self.settings_ui.open_settings(0),
                default=True,
            ),
            pystray.MenuItem(
                "Pause",
                lambda icon, item: self._toggle_paused(),
                checked=lambda item: self._paused,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Cloud",
                lambda icon, item: self._switch_engine_mode("cloud"),
                checked=lambda item: self.config_mgr.data.engine_mode == "cloud",
                radio=True,
            ),
            pystray.MenuItem(
                "Offline",
                lambda icon, item: self._switch_engine_mode("local"),
                checked=lambda item: self.config_mgr.data.engine_mode == "local",
                radio=True,
            ),
            pystray.MenuItem(
                "Provider",
                pystray.Menu(*(self._make_provider_item(p) for p in SUPPORTED_PROVIDERS)),
            ),
            pystray.MenuItem(
                "Language",
                pystray.Menu(
                    pystray.MenuItem(
                        "English",
                        lambda icon, item: self._set_language("en"),
                        checked=lambda item: self.config_mgr.data.language == "en",
                        radio=True,
                    ),
                    pystray.MenuItem(
                        "Hinglish",
                        lambda icon, item: self._set_language("hinglish"),
                        checked=lambda item: self.config_mgr.data.language == "hinglish",
                        radio=True,
                    ),
                    pystray.MenuItem(
                        "Hindi",
                        lambda icon, item: self._set_language("hi"),
                        checked=lambda item: self.config_mgr.data.language == "hi",
                        radio=True,
                    ),
                    pystray.MenuItem(
                        "Auto",
                        lambda icon, item: self._set_language("auto"),
                        checked=lambda item: self.config_mgr.data.language == "auto",
                        radio=True,
                    ),
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Remove Filler Words",
                lambda icon, item: self._toggle_fillers(),
                checked=lambda item: self.config_mgr.data.remove_fillers,
            ),
            pystray.MenuItem(
                "Style",
                pystray.Menu(
                    pystray.MenuItem(
                        "Natural",
                        lambda icon, item: self._set_style_mode("clean"),
                        checked=lambda item: self.config_mgr.data.style_mode == "clean",
                        radio=True,
                    ),
                    pystray.MenuItem(
                        "Polished",
                        lambda icon, item: self._set_style_mode("polished"),
                        checked=lambda item: self.config_mgr.data.style_mode == "polished",
                        radio=True,
                    ),
                    pystray.MenuItem(
                        "Raw",
                        lambda icon, item: self._set_style_mode("raw"),
                        checked=lambda item: self.config_mgr.data.style_mode == "raw",
                        radio=True,
                    ),
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Copy Last",
                lambda icon, item: self._copy_last_dictation(),
            ),
            pystray.MenuItem(
                "History",
                lambda icon, item: self.settings_ui.open_settings(2),
            ),
            pystray.MenuItem(
                "Launch at Startup",
                lambda icon, item: self._toggle_startup(),
                checked=lambda item: is_startup_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda icon, item: self.shutdown()),
        )

    def run(self) -> int:
        if not self._acquire_single_instance():
            self.settings_ui.open_settings(0)
            return 0

        self._start_shutdown_watcher()
        self.overlay.start()
        self.settings_ui.start()
        self.hotkey.start()

        cfg = self.config_mgr.data
        if self.engine.needs_local_stt(cfg):
            self.engine.preload_local_model_async(cfg)
        if cfg.engine_mode == "cloud":
            self.engine.warm_cloud_connection_async(cfg)
            mode_badge = PROVIDER_SPECS.get(cfg.cloud_provider, {}).get("badge", "Cloud")
        else:
            mode_badge = "Offline"
        if cfg.language == "hinglish":
            mode_badge = f"{mode_badge} • Hinglish"

        self.overlay.show_toast(f"WinFlow • Alt+X ({mode_badge})", duration_s=1.4)

        if self._first_run and os.environ.get("WINFLOW_NO_FIRST_RUN_UI") != "1":
            self.settings_ui.open_settings(0)

        self.tray = pystray.Icon(
            "winflow",
            icon=_create_tray_icon(mode=cfg.engine_mode),
            title="WinFlow (Alt+X)",
            menu=self._build_menu(),
        )
        self._refresh_tray_icon(recording=False)
        self.tray.run()
        return 0

    def shutdown(self) -> None:
        if self._shutdown_event:
            kernel32.CloseHandle(self._shutdown_event)
            self._shutdown_event = None
        self._cancel_max_timer()
        self.hotkey.stop()
        self.recorder.cancel()
        self.overlay.stop()
        self.settings_ui.stop()
        self.engine.close()
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self.tray:
            self.tray.stop()


def main() -> None:
    app = WinFlowApp()
    raise SystemExit(app.run())


if __name__ == "__main__":
    main()
