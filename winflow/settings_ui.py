from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Callable

from .audio import list_input_devices
from .config import PROJECT_ROOT, ConfigManager, is_startup_enabled, set_startup_enabled
from .injector import set_clipboard_text

BG_MAIN = "#14161F"
BG_CARD = "#1E2130"
BG_INPUT = "#282C3E"
FG_PRIMARY = "#F2F4FA"
FG_MUTED = "#9BA3B8"
ACCENT = "#5B8CFF"
ACCENT_HOVER = "#4778F5"
BORDER = "#2E3348"


class SettingsWindowController:
    """
    Runs a single persistent hidden Tk root on a dedicated UI thread so opening
    and closing the Settings/History window any number of times never hits Tcl thread issues.
    """

    def __init__(
        self,
        config_mgr: ConfigManager,
        on_config_saved: Callable[[], None],
    ) -> None:
        self.config_mgr = config_mgr
        self.on_config_saved = on_config_saved
        self._cmd_queue: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._root: tk.Tk | None = None
        self._win: tk.Toplevel | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._tk_loop, name="WinFlow-SettingsUI", daemon=True
        )
        self._thread.start()

    def open_settings(self, tab_index: int = 0) -> None:
        self._cmd_queue.put(f"open:{tab_index}")

    def stop(self) -> None:
        self._cmd_queue.put("quit")

    def _tk_loop(self) -> None:
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        root = tk.Tk()
        root.withdraw()
        self._root = root
        self._apply_theme(root)

        def _poll() -> None:
            while not self._cmd_queue.empty():
                try:
                    cmd = self._cmd_queue.get_nowait()
                except queue.Empty:
                    break
                if cmd == "quit":
                    if self._win and self._win.winfo_exists():
                        self._win.destroy()
                    root.quit()
                    return
                if cmd.startswith("open:"):
                    idx = int(cmd.split(":", 1)[1])
                    self._show_window(idx)
            root.after(120, _poll)

        root.after(120, _poll)
        root.mainloop()

    def _apply_theme(self, root: tk.Tk) -> None:
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(".", background=BG_MAIN, foreground=FG_PRIMARY, font=("Segoe UI", 10))
        style.configure("TFrame", background=BG_MAIN)
        style.configure("Card.TFrame", background=BG_CARD)
        style.configure("TLabel", background=BG_MAIN, foreground=FG_PRIMARY)
        style.configure("Card.TLabel", background=BG_CARD, foreground=FG_PRIMARY)
        style.configure("Muted.TLabel", background=BG_CARD, foreground=FG_MUTED, font=("Segoe UI", 9))
        style.configure(
            "Header.TLabel",
            background=BG_MAIN,
            foreground=FG_PRIMARY,
            font=("Segoe UI Semibold", 14),
        )
        style.configure(
            "Section.TLabel",
            background=BG_CARD,
            foreground="#8AB4FF",
            font=("Segoe UI Semibold", 10),
        )
        style.configure(
            "TNotebook", background=BG_MAIN, borderwidth=0, tabmargins=[0, 0, 0, 8]
        )
        style.configure(
            "TNotebook.Tab",
            background=BG_CARD,
            foreground=FG_MUTED,
            padding=[14, 7],
            font=("Segoe UI Semibold", 10),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", ACCENT)],
            foreground=[("selected", "#FFFFFF")],
        )
        style.configure(
            "Accent.TButton",
            background=ACCENT,
            foreground="#FFFFFF",
            borderwidth=0,
            padding=[16, 7],
            font=("Segoe UI Semibold", 10),
        )
        style.map("Accent.TButton", background=[("active", ACCENT_HOVER)])
        style.configure(
            "Secondary.TButton",
            background=BG_INPUT,
            foreground=FG_PRIMARY,
            borderwidth=0,
            padding=[10, 5],
        )
        style.map("Secondary.TButton", background=[("active", BORDER)])
        style.configure(
            "Card.TCheckbutton",
            background=BG_CARD,
            foreground=FG_PRIMARY,
            font=("Segoe UI", 10),
        )
        style.map("Card.TCheckbutton", background=[("active", BG_CARD)])
        style.configure(
            "Card.TRadiobutton",
            background=BG_CARD,
            foreground=FG_PRIMARY,
            font=("Segoe UI", 10),
        )
        style.map("Card.TRadiobutton", background=[("active", BG_CARD)])
        style.configure(
            "TCombobox",
            fieldbackground=BG_INPUT,
            background=BG_INPUT,
            foreground=FG_PRIMARY,
            arrowcolor=FG_PRIMARY,
            borderwidth=1,
            relief="flat",
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", BG_INPUT)],
            selectbackground=[("readonly", BG_INPUT)],
            selectforeground=[("readonly", FG_PRIMARY)],
            foreground=[("readonly", FG_PRIMARY)],
            background=[("readonly", BG_INPUT)],
        )
        root.option_add("*TCombobox*Listbox.background", BG_INPUT)
        root.option_add("*TCombobox*Listbox.foreground", FG_PRIMARY)
        root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        root.option_add("*TCombobox*Listbox.selectForeground", "#FFFFFF")

    def _show_window(self, tab_index: int = 0) -> None:
        assert self._root is not None
        if self._win is not None and self._win.winfo_exists():
            self._win.deiconify()
            self._win.lift()
            self._win.focus_force()
            if hasattr(self, "_notebook"):
                self._notebook.select(tab_index)
            return

        cfg = self.config_mgr.data
        win = tk.Toplevel(self._root)
        self._win = win
        win.title("WinFlow — Alt+X AI Dictation")
        win.configure(bg=BG_MAIN)
        ico_path = PROJECT_ROOT / "assets" / "winflow.ico"
        if ico_path.exists():
            try:
                win.iconbitmap(str(ico_path))
            except Exception:
                pass
        try:
            import ctypes

            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
            dark_val = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(dark_val), ctypes.sizeof(dark_val)
            )
        except Exception:
            pass
        win.geometry("660x610")
        win.minsize(630, 560)

        # Center on screen
        win.update_idletasks()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        win.geometry(f"650x610+{(sw - 650) // 2}+{(sh - 610) // 2}")

        header = ttk.Frame(win, padding=(20, 16, 20, 8))
        header.pack(fill="x")
        ttk.Label(header, text="WinFlow Settings & History", style="Header.TLabel").pack(
            anchor="w"
        )
        ttk.Label(
            header,
            text="Press Alt + X for WinFlow AI Dictation  •  Win + H stays Native Windows Voice Typing.",
            foreground=FG_MUTED,
        ).pack(anchor="w", pady=(2, 0))

        nb = ttk.Notebook(win)
        self._notebook = nb
        nb.pack(fill="both", expand=True, padx=20, pady=(6, 10))

        # --- Variables ---
        v_engine_mode = tk.StringVar(value=cfg.engine_mode)
        v_cloud_provider = tk.StringVar(value=cfg.cloud_provider)
        v_groq_key = tk.StringVar(value=cfg.groq_api_key)
        v_gemini_key = tk.StringVar(value=cfg.gemini_api_key)
        v_local_model = tk.StringVar(value=cfg.local_model_size)
        v_local_device = tk.StringVar(value=cfg.local_device)
        v_language = tk.StringVar(value=cfg.language)

        v_hotkey_mode = tk.StringVar(value=cfg.hotkey_mode)
        v_remove_fillers = tk.BooleanVar(value=cfg.remove_fillers)
        v_smart_fmt = tk.BooleanVar(value=cfg.smart_formatting)
        v_style_mode = tk.StringVar(value=cfg.style_mode)
        v_custom_dict = tk.StringVar(value=cfg.custom_dictionary)
        v_restore_clip = tk.BooleanVar(value=cfg.restore_clipboard)
        v_sound_cues = tk.BooleanVar(value=cfg.sound_cues)
        v_startup = tk.BooleanVar(value=is_startup_enabled())

        mic_devices = list_input_devices()
        mic_labels = [label for _, label in mic_devices]
        current_mic_idx = 0
        for i, (dev_id, _) in enumerate(mic_devices):
            if dev_id == cfg.input_device:
                current_mic_idx = i
                break
        v_mic_label = tk.StringVar(value=mic_labels[current_mic_idx])

        # ==================== TAB 1: Engine & API Keys ====================
        tab_engine = ttk.Frame(nb, padding=4)
        nb.add(tab_engine, text="  Engine & Cloud/Offline  ")

        card_mode = ttk.Frame(tab_engine, style="Card.TFrame", padding=14)
        card_mode.pack(fill="x", pady=(4, 10))
        ttk.Label(card_mode, text="ACTIVE SPEECH ENGINE", style="Section.TLabel").pack(anchor="w")
        ttk.Radiobutton(
            card_mode,
            text="Free Cloud API (Groq / Gemini) — Near-instant (<0.5s), zero RAM/GPU load, AI LLM cleanup",
            variable=v_engine_mode,
            value="cloud",
            style="Card.TRadiobutton",
        ).pack(anchor="w", pady=(8, 4))
        ttk.Radiobutton(
            card_mode,
            text="100% Offline Local AI (faster-whisper) — Runs privately on your PC, no internet or API key",
            variable=v_engine_mode,
            value="local",
            style="Card.TRadiobutton",
        ).pack(anchor="w")

        card_cloud = ttk.Frame(tab_engine, style="Card.TFrame", padding=14)
        card_cloud.pack(fill="x", pady=(0, 10))
        ttk.Label(card_cloud, text="FREE CLOUD API CONFIGURATION", style="Section.TLabel").pack(
            anchor="w"
        )

        prov_row = ttk.Frame(card_cloud, style="Card.TFrame")
        prov_row.pack(fill="x", pady=(8, 6))
        ttk.Label(prov_row, text="Cloud Provider:", style="Card.TLabel", width=16).pack(
            side="left"
        )
        ttk.Radiobutton(
            prov_row,
            text="Groq (Whisper Large v3 Turbo + Qwen 3.8)",
            variable=v_cloud_provider,
            value="groq",
            style="Card.TRadiobutton",
        ).pack(side="left", padx=(0, 14))
        ttk.Radiobutton(
            prov_row,
            text="Google Gemini 2.5 Flash",
            variable=v_cloud_provider,
            value="gemini",
            style="Card.TRadiobutton",
        ).pack(side="left")

        # Groq Key row
        groq_row = ttk.Frame(card_cloud, style="Card.TFrame")
        groq_row.pack(fill="x", pady=4)
        ttk.Label(groq_row, text="Groq API Key:", style="Card.TLabel", width=16).pack(side="left")
        groq_entry = tk.Entry(
            groq_row,
            textvariable=v_groq_key,
            show="•",
            bg=BG_INPUT,
            fg=FG_PRIMARY,
            insertbackground=FG_PRIMARY,
            relief="flat",
            font=("Consolas", 10),
        )
        groq_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))
        ttk.Button(
            groq_row,
            text="Get Free Key ↗",
            style="Secondary.TButton",
            command=lambda: webbrowser.open("https://console.groq.com/keys"),
        ).pack(side="left")

        # Gemini Key row
        gem_row = ttk.Frame(card_cloud, style="Card.TFrame")
        gem_row.pack(fill="x", pady=4)
        ttk.Label(gem_row, text="Gemini API Key:", style="Card.TLabel", width=16).pack(side="left")
        gem_entry = tk.Entry(
            gem_row,
            textvariable=v_gemini_key,
            show="•",
            bg=BG_INPUT,
            fg=FG_PRIMARY,
            insertbackground=FG_PRIMARY,
            relief="flat",
            font=("Consolas", 10),
        )
        gem_entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))
        ttk.Button(
            gem_row,
            text="Get Free Key ↗",
            style="Secondary.TButton",
            command=lambda: webbrowser.open("https://aistudio.google.com/apikey"),
        ).pack(side="left")

        card_local = ttk.Frame(tab_engine, style="Card.TFrame", padding=14)
        card_local.pack(fill="x")
        ttk.Label(card_local, text="OFFLINE LOCAL WHISPER MODEL", style="Section.TLabel").pack(
            anchor="w"
        )
        loc_row = ttk.Frame(card_local, style="Card.TFrame")
        loc_row.pack(fill="x", pady=(8, 2))
        ttk.Label(loc_row, text="Model Size:", style="Card.TLabel").pack(side="left")
        ttk.Combobox(
            loc_row,
            textvariable=v_local_model,
            values=["tiny", "base", "small", "distil-large-v3"],
            state="readonly",
            width=15,
        ).pack(side="left", padx=(6, 18))

        ttk.Label(loc_row, text="Compute:", style="Card.TLabel").pack(side="left")
        ttk.Combobox(
            loc_row,
            textvariable=v_local_device,
            values=["auto", "cpu", "cuda"],
            state="readonly",
            width=8,
        ).pack(side="left", padx=(6, 18))

        ttk.Label(loc_row, text="Language:", style="Card.TLabel").pack(side="left")
        ttk.Combobox(
            loc_row,
            textvariable=v_language,
            values=["en", "auto", "hi", "es", "fr", "de"],
            state="readonly",
            width=7,
        ).pack(side="left", padx=(6, 0))
        ttk.Label(
            card_local,
            text="Tip: 'base' (~145 MB) is fast & accurate on your PC; unloads from RAM automatically in Cloud mode.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 0))

        # ==================== TAB 2: AI Cleanup & Alt+X ====================
        tab_ai = ttk.Frame(nb, padding=4)
        nb.add(tab_ai, text="  AI Cleanup & Alt+X  ")

        card_clean = ttk.Frame(tab_ai, style="Card.TFrame", padding=14)
        card_clean.pack(fill="x", pady=(4, 10))
        ttk.Label(card_clean, text="WISPR-STYLE AI CLEANUP", style="Section.TLabel").pack(
            anchor="w"
        )
        ttk.Checkbutton(
            card_clean,
            text="Remove filler words ('um', 'uh', 'like', 'you know') & accidental stutters ('can Can')",
            variable=v_remove_fillers,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=(8, 4))
        ttk.Checkbutton(
            card_clean,
            text="Auto-fix punctuation, capitalization & voice commands ('new line', 'new paragraph')",
            variable=v_smart_fmt,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=(0, 8))

        style_row = ttk.Frame(card_clean, style="Card.TFrame")
        style_row.pack(fill="x", pady=2)
        ttk.Label(style_row, text="Output Style:", style="Card.TLabel", width=16).pack(side="left")
        ttk.Combobox(
            style_row,
            textvariable=v_style_mode,
            values=["clean", "polished", "raw"],
            state="readonly",
            width=16,
        ).pack(side="left")
        ttk.Label(
            style_row,
            text="  (clean = exact voice, polished = smooth, raw = verbatim)",
            style="Muted.TLabel",
        ).pack(side="left")

        dict_row = ttk.Frame(card_clean, style="Card.TFrame")
        dict_row.pack(fill="x", pady=(8, 0))
        ttk.Label(dict_row, text="Custom Words:", style="Card.TLabel", width=16).pack(side="left")
        tk.Entry(
            dict_row,
            textvariable=v_custom_dict,
            bg=BG_INPUT,
            fg=FG_PRIMARY,
            insertbackground=FG_PRIMARY,
            relief="flat",
            font=("Segoe UI", 10),
        ).pack(side="left", fill="x", expand=True, ipady=4)

        card_hk = ttk.Frame(tab_ai, style="Card.TFrame", padding=14)
        card_hk.pack(fill="x")
        ttk.Label(card_hk, text="SHORTCUT, MICROPHONE & SYSTEM", style="Section.TLabel").pack(
            anchor="w"
        )

        hk_row = ttk.Frame(card_hk, style="Card.TFrame")
        hk_row.pack(fill="x", pady=(8, 6))
        ttk.Label(hk_row, text="Alt + X Behavior:", style="Card.TLabel", width=16).pack(
            side="left"
        )
        ttk.Combobox(
            hk_row,
            textvariable=v_hotkey_mode,
            values=["hybrid", "toggle", "hold"],
            state="readonly",
            width=14,
        ).pack(side="left")
        ttk.Label(
            hk_row,
            text="  (hybrid = tap to toggle OR hold to push-to-talk; Esc cancels)",
            style="Muted.TLabel",
        ).pack(side="left")

        mic_row = ttk.Frame(card_hk, style="Card.TFrame")
        mic_row.pack(fill="x", pady=(0, 8))
        ttk.Label(mic_row, text="Microphone:", style="Card.TLabel", width=16).pack(side="left")
        ttk.Combobox(
            mic_row,
            textvariable=v_mic_label,
            values=mic_labels,
            state="readonly",
            width=45,
        ).pack(side="left", fill="x", expand=True)

        ttk.Checkbutton(
            card_hk,
            text="Restore my previous clipboard contents automatically after pasting",
            variable=v_restore_clip,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            card_hk,
            text="Play subtle start/stop audio chime",
            variable=v_sound_cues,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(
            card_hk,
            text="Start WinFlow automatically when I sign into Windows",
            variable=v_startup,
            style="Card.TCheckbutton",
        ).pack(anchor="w", pady=2)

        # ==================== TAB 3: History ====================
        tab_hist = ttk.Frame(nb, padding=4)
        nb.add(tab_hist, text="  Recent Dictations  ")

        hist_card = ttk.Frame(tab_hist, style="Card.TFrame", padding=12)
        hist_card.pack(fill="both", expand=True, pady=4)

        if not cfg.history:
            ttk.Label(
                hist_card,
                text="No dictations yet — press Alt + X anywhere and start speaking!",
                style="Muted.TLabel",
            ).pack(pady=30)
        else:
            canvas = tk.Canvas(hist_card, bg=BG_CARD, highlightthickness=0)
            scrollbar = ttk.Scrollbar(hist_card, orient="vertical", command=canvas.yview)
            inner = ttk.Frame(canvas, style="Card.TFrame")
            inner.bind(
                "<Configure>",
                lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
            )
            canvas.create_window((0, 0), window=inner, anchor="nw", width=565)
            canvas.configure(yscrollcommand=scrollbar.set)
            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            for item in cfg.history[:20]:
                row = tk.Frame(inner, bg=BG_INPUT, padx=10, pady=8)
                row.pack(fill="x", pady=4)
                meta = f"{item.get('timestamp', '')}  •  {item.get('mode', '')}  •  {item.get('latency_ms', 0)} ms"
                tk.Label(
                    row, text=meta, bg=BG_INPUT, fg=FG_MUTED, font=("Segoe UI", 8)
                ).pack(anchor="w")
                txt = item.get("cleaned", "")
                tk.Label(
                    row,
                    text=txt,
                    bg=BG_INPUT,
                    fg=FG_PRIMARY,
                    font=("Segoe UI", 10),
                    wraplength=460,
                    justify="left",
                ).pack(side="left", anchor="w", pady=(3, 0))
                ttk.Button(
                    row,
                    text="Copy",
                    style="Secondary.TButton",
                    command=lambda t=txt: set_clipboard_text(t),
                ).pack(side="right", padx=(8, 0))

        # ==================== Bottom Action Bar ====================
        footer = ttk.Frame(win, padding=(20, 4, 20, 16))
        footer.pack(fill="x")
        status_lbl = ttk.Label(footer, text="", foreground="#4ADE80")
        status_lbl.pack(side="left")

        def _save_and_close() -> None:
            chosen_mic = None
            for dev_id, lbl in mic_devices:
                if lbl == v_mic_label.get():
                    chosen_mic = dev_id
                    break

            chosen_mode = v_engine_mode.get()
            # If user pasted an API key and stayed on cloud (or pasted key for the first time), honor it;
            # if they chose cloud without a key, warn gently instead of failing silently later.
            prov = v_cloud_provider.get()
            groq_k = v_groq_key.get().strip()
            gem_k = v_gemini_key.get().strip()
            if chosen_mode == "cloud":
                if prov == "groq" and not groq_k and gem_k:
                    prov = "gemini"
                elif prov == "gemini" and not gem_k and groq_k:
                    prov = "groq"
                elif (prov == "groq" and not groq_k) or (prov == "gemini" and not gem_k):
                    status_lbl.configure(
                        text="Paste a Groq or Gemini key for Cloud mode, or pick Offline Local.",
                        foreground="#FF7B7B",
                    )
                    nb.select(0)
                    return

            self.config_mgr.update(
                engine_mode=chosen_mode,
                cloud_provider=prov,
                groq_api_key=groq_k,
                gemini_api_key=gem_k,
                local_model_size=v_local_model.get(),
                local_device=v_local_device.get(),
                language=v_language.get(),
                hotkey_mode=v_hotkey_mode.get(),
                remove_fillers=v_remove_fillers.get(),
                smart_formatting=v_smart_fmt.get(),
                style_mode=v_style_mode.get(),
                custom_dictionary=v_custom_dict.get().strip(),
                restore_clipboard=v_restore_clip.get(),
                sound_cues=v_sound_cues.get(),
                input_device=chosen_mic,
            )
            set_startup_enabled(v_startup.get())
            self.on_config_saved()
            win.destroy()

        ttk.Button(footer, text="Save Changes", style="Accent.TButton", command=_save_and_close).pack(
            side="right"
        )
        ttk.Button(
            footer, text="Cancel", style="Secondary.TButton", command=win.destroy
        ).pack(side="right", padx=(0, 8))

        nb.select(tab_index)
