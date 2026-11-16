"""Generate crisp multi-size app icon (winflow.ico) and real product/UI/installer screenshots for GitHub README."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

os.environ.setdefault("WINFLOW_INSTANCE_ID", f"_assets_{os.getpid()}")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from winflow.config import ConfigManager
from winflow.settings_ui import MINIMALIST_HTML, _apply_dark_dwm_and_icon, _SettingsJsApi

ASSETS_DIR = PROJECT_ROOT / "assets"
DOCS_IMG_DIR = PROJECT_ROOT / "docs" / "images"


def load_font(size: int, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if mono:
        names = ["consolab.ttf", "consola.ttf"] if bold else ["consola.ttf"]
    elif bold:
        names = ["segoeuib.ttf", "arialbd.ttf"]
    else:
        names = ["segoeui.ttf", "arial.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()


def draw_logo_mark(size: int = 512) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = int(size * 0.06)
    r = int(size * 0.24)
    for step in range(size - 2 * pad):
        t = step / max(1, (size - 2 * pad - 1))
        col = (
            int(58 + 38 * (1 - t)),
            int(104 + 44 * (1 - t)),
            int(242 + 13 * (1 - t)),
            255,
        )
        y = pad + step
        d.line([(pad, y), (size - pad, y)], fill=col)

    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((pad, pad, size - pad, size - pad), radius=r, fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)

    od = ImageDraw.Draw(out)
    od.rounded_rectangle(
        (pad, pad, size - pad, size - pad),
        radius=r,
        outline=(165, 198, 255, 110),
        width=max(2, size // 85),
    )

    cx = size // 2
    cy = size // 2
    bar_w = max(4, int(size * 0.072))
    gap = int(size * 0.125)
    heights = [0.24, 0.44, 0.60, 0.38, 0.22]
    offsets = [-2, -1, 0, 1, 2]
    for off, h_frac in zip(offsets, heights):
        bx = cx + off * gap
        bh = int(size * h_frac)
        od.rounded_rectangle(
            (bx - bar_w // 2, cy - bh // 2, bx + bar_w // 2, cy + bh // 2),
            radius=bar_w // 2,
            fill=(255, 255, 255, 250),
        )
    return out


def render_pill_card(state: str, label: str, bars: list[float] | None = None, scale: int = 2) -> Image.Image:
    pw, ph = 296 * scale, 54 * scale
    img = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = 4 * scale
    border_color = {
        "listening": (255, 92, 92, 200),
        "holding": (255, 146, 72, 205),
        "processing": (99, 140, 255, 205),
        "success": (62, 207, 142, 205),
    }[state]

    draw.rounded_rectangle(
        (pad, pad, pw - pad, ph - pad),
        radius=(ph - 2 * pad) // 2,
        fill=(20, 21, 28, 248),
        outline=border_color,
        width=2 * scale,
    )

    cy = ph // 2
    left_x = 19 * scale
    font_main = load_font(13 * scale, bold=True)

    if state in ("listening", "holding"):
        bar_vals = bars or [0.42, 0.82, 0.98, 0.68, 0.36]
        bar_col = (255, 96, 96, 255) if state == "listening" else (255, 152, 76, 255)
        for i, v in enumerate(bar_vals):
            bh = int((6 + v * 17) * scale)
            bx = left_x + i * (7 * scale)
            draw.rounded_rectangle(
                (bx, cy - bh // 2, bx + int(3.6 * scale), cy + bh // 2),
                radius=2 * scale,
                fill=bar_col,
            )
        text_x = left_x + 42 * scale
    elif state == "processing":
        radii = [3.5, 5.2, 3.8]
        alphas = [165, 255, 185]
        for i, (rad, al) in enumerate(zip(radii, alphas)):
            r = int(rad * scale)
            cx = left_x + (6 + i * 11) * scale
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(112, 156, 255, al))
        text_x = left_x + 40 * scale
    else:
        cx = left_x + 10 * scale
        r = 8 * scale
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(46, 196, 126, 255))
        draw.line(
            [
                (cx - 3.5 * scale, cy + 0.5 * scale),
                (cx - 1 * scale, cy + 3 * scale),
                (cx + 4 * scale, cy - 2.5 * scale),
            ],
            fill=(255, 255, 255, 255),
            width=2 * scale,
        )
        text_x = left_x + 27 * scale

    draw.text((text_x, cy), label, font=font_main, fill=(244, 246, 252, 255), anchor="lm")
    return img.resize((pw // 2, ph // 2), Image.Resampling.LANCZOS)


def draw_keycap(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, accent: bool = False) -> int:
    f = load_font(18, bold=True, mono=True)
    bbox = draw.textbbox((0, 0), text, font=f)
    tw = bbox[2] - bbox[0]
    w = tw + 28
    h = 38
    bg = (42, 66, 130, 255) if accent else (30, 34, 49, 255)
    bd = (108, 152, 255, 255) if accent else (66, 74, 102, 255)
    draw.rounded_rectangle((x, y + 2, x + w, y + h + 2), radius=9, fill=(8, 10, 16, 200))
    draw.rounded_rectangle((x, y, x + w, y + h), radius=9, fill=bg, outline=bd, width=2)
    draw.text((x + w // 2, y + h // 2), text, font=f, fill=(245, 248, 255, 255), anchor="mm")
    return w


def build_hero_banner() -> None:
    W, H = 1280, 620
    base = Image.new("RGBA", (W, H), (11, 13, 21, 255))

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((680, -80, 1320, 520), fill=(66, 112, 255, 52))
    gd.ellipse((-120, 260, 520, 760), fill=(38, 196, 138, 32))
    glow = glow.filter(ImageFilter.GaussianBlur(85))
    base = Image.alpha_composite(base, glow)

    d = ImageDraw.Draw(base)

    for gx in range(0, W, 64):
        d.line([(gx, 0), (gx, H)], fill=(23, 27, 42, 255), width=1)
    for gy in range(0, H, 64):
        d.line([(0, gy), (W, gy)], fill=(23, 27, 42, 255), width=1)

    logo = draw_logo_mark(84)
    base.alpha_composite(logo, (72, 68))
    d.text((172, 92), "WinFlow", font=load_font(44, bold=True), fill=(246, 248, 255, 255), anchor="lm")
    d.rounded_rectangle((366, 76, 456, 108), radius=16, fill=(32, 48, 92, 240), outline=(92, 138, 255, 190), width=1)
    d.text((411, 92), "v1.4.0", font=load_font(14, bold=True, mono=True), fill=(158, 192, 255, 255), anchor="mm")

    d.text((76, 188), "AI Voice Dictation", font=load_font(48, bold=True), fill=(248, 250, 255, 255))
    d.text((76, 246), "for Windows 10 & 11.", font=load_font(48, bold=True), fill=(116, 162, 255, 255))

    d.text(
        (76, 326),
        "Standalone .exe installer • Cloud & Offline • Hinglish & English.",
        font=load_font(20),
        fill=(172, 181, 204, 255),
    )

    kx = 76
    ky = 392
    w1 = draw_keycap(d, kx, ky, "Alt", accent=True)
    d.text((kx + w1 + 12, ky + 19), "+", font=load_font(20, bold=True), fill=(155, 165, 190, 255), anchor="mm")
    w2 = draw_keycap(d, kx + w1 + 24, ky, "X", accent=True)
    d.text(
        (kx + w1 + 24 + w2 + 16, ky + 19),
        "Speak & Paste Anywhere",
        font=load_font(17, bold=True),
        fill=(232, 238, 252, 255),
        anchor="lm",
    )

    ky2 = 446
    w3 = draw_keycap(d, kx, ky2, "Win", accent=False)
    d.text((kx + w3 + 12, ky2 + 19), "+", font=load_font(20, bold=True), fill=(130, 140, 165, 255), anchor="mm")
    w4 = draw_keycap(d, kx + w3 + 24, ky2, "H", accent=False)
    d.text(
        (kx + w3 + 24 + w4 + 16, ky2 + 19),
        "Native Windows Voice Typing Untouched",
        font=load_font(16),
        fill=(154, 164, 188, 255),
        anchor="lm",
    )

    stats = [
        ("WinFlow-Setup.exe", "One-Click Native Installer", (98, 152, 255)),
        ("8 Cloud + Offline", "Groq, Gemini, GPT, Claude, Grok…", (58, 212, 148)),
        ("Hinglish + English", "Licensed by Piyush Maurya", (255, 146, 92)),
    ]
    sx = 76
    for title, sub, dot_col in stats:
        d.rounded_rectangle((sx, 516, sx + 206, 578), radius=14, fill=(20, 23, 35, 245), outline=(44, 51, 76, 255), width=1)
        d.ellipse((sx + 16, 531, sx + 26, 541), fill=dot_col)
        d.text((sx + 34, 536), title, font=load_font(14, bold=True), fill=(242, 246, 255, 255), anchor="lm")
        d.text((sx + 16, 560), sub, font=load_font(12), fill=(146, 156, 182, 255), anchor="lm")
        sx += 218

    card_x1, card_y1, card_x2, card_y2 = 720, 88, 1212, 482
    d.rounded_rectangle((card_x1, card_y1, card_x2, card_y2), radius=20, fill=(20, 23, 34, 245), outline=(54, 62, 92, 255), width=2)
    for i, col in enumerate([(239, 68, 68), (245, 158, 11), (34, 197, 94)]):
        cx = card_x1 + 26 + i * 20
        cy = card_y1 + 24
        d.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), fill=col)
    d.text((card_x1 + 98, card_y1 + 24), "Any Windows App", font=load_font(13, bold=True), fill=(142, 152, 178, 255), anchor="lm")
    d.line([(card_x1, card_y1 + 48), (card_x2, card_y1 + 48)], fill=(42, 48, 70, 255), width=1)

    d.text((card_x1 + 26, card_y1 + 74), "SPOKEN", font=load_font(11, bold=True), fill=(255, 118, 118, 255))
    d.rounded_rectangle((card_x1 + 24, card_y1 + 96, card_x2 - 24, card_y1 + 190), radius=12, fill=(30, 26, 36, 255), outline=(92, 52, 62, 220), width=1)
    raw_lines = [
        '"Um, hey team, can can we ship the release',
        'build today? New line let me let me know."',
    ]
    for idx, ln in enumerate(raw_lines):
        d.text((card_x1 + 40, card_y1 + 122 + idx * 24), ln, font=load_font(14), fill=(212, 186, 192, 255))

    d.text((card_x1 + 26, card_y1 + 214), "PASTED AT CURSOR", font=load_font(11, bold=True), fill=(74, 222, 148, 255))
    d.rounded_rectangle((card_x1 + 24, card_y1 + 236, card_x2 - 24, card_y1 + 344), radius=12, fill=(22, 35, 34, 255), outline=(52, 132, 96, 220), width=1)
    clean_lines = [
        "Hey team, can we ship the release build today?",
        "",
        "Let me know.|",
    ]
    for idx, ln in enumerate(clean_lines):
        d.text((card_x1 + 40, card_y1 + 256 + idx * 24), ln, font=load_font(15, bold=(idx == 0)), fill=(238, 252, 244, 255))

    pill_listen = render_pill_card("listening", "Listening… (Alt+X)", scale=2)
    px = (card_x1 + card_x2 - pill_listen.width) // 2
    py = card_y2 - 22
    base.alpha_composite(pill_listen, (px, py))

    base.convert("RGB").save(DOCS_IMG_DIR / "hero-banner.png", quality=95)


def build_floating_pills_showcase() -> None:
    W, H = 1200, 320
    canvas = Image.new("RGBA", (W, H), (14, 16, 25, 255))
    d = ImageDraw.Draw(canvas)

    d.text(
        (W // 2, 42),
        "Floating Status Pill — Zero Focus Steal",
        font=load_font(22, bold=True),
        fill=(242, 245, 255, 255),
        anchor="mm",
    )

    states = [
        ("listening", "Listening… (Alt+X)", "Tap Alt+X", [0.35, 0.85, 1.0, 0.72, 0.40]),
        ("holding", "Release to paste", "Hold Alt+X", [0.55, 0.75, 0.92, 0.88, 0.50]),
        ("processing", "Transcribing…", "Processing", None),
        ("success", "Pasted", "Done", None),
    ]

    positions = [(70, 108), (634, 108), (70, 212), (634, 212)]
    for (st, lbl, caption, bars), (cx, cy) in zip(states, positions):
        d.rounded_rectangle((cx, cy - 18, cx + 496, cy + 72), radius=16, fill=(22, 25, 38, 255), outline=(44, 50, 74, 255), width=1)
        pill = render_pill_card(st, lbl, bars=bars, scale=2)
        canvas.alpha_composite(pill, (cx + 16, cy - 1))
        d.text((cx + 330, cy + 27), caption, font=load_font(13, bold=True), fill=(182, 192, 216, 255), anchor="lm")

    canvas.convert("RGB").save(DOCS_IMG_DIR / "floating-pills-showcase.png", quality=95)


def _capture_hwnd_rgb(hwnd: int) -> Image.Image:
    rect = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    ww = rect.right - rect.left
    wh = rect.bottom - rect.top

    hdc_win = ctypes.windll.user32.GetWindowDC(hwnd)
    hdc_mem = ctypes.windll.gdi32.CreateCompatibleDC(hdc_win)
    hbmp = ctypes.windll.gdi32.CreateCompatibleBitmap(hdc_win, ww, wh)
    old = ctypes.windll.gdi32.SelectObject(hdc_mem, hbmp)
    ctypes.windll.user32.PrintWindow(hwnd, hdc_mem, 2)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD),
            ("biWidth", wt.LONG),
            ("biHeight", wt.LONG),
            ("biPlanes", wt.WORD),
            ("biBitCount", wt.WORD),
            ("biCompression", wt.DWORD),
            ("biSizeImage", wt.DWORD),
            ("biXPelsPerMeter", wt.LONG),
            ("biYPelsPerMeter", wt.LONG),
            ("biClrUsed", wt.DWORD),
            ("biClrImportant", wt.DWORD),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = ww
    bmi.biHeight = -wh
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    buf = ctypes.create_string_buffer(ww * wh * 4)
    ctypes.windll.gdi32.GetDIBits(hdc_mem, hbmp, 0, wh, buf, ctypes.byref(bmi), 0)

    ctypes.windll.gdi32.SelectObject(hdc_mem, old)
    ctypes.windll.gdi32.DeleteObject(hbmp)
    ctypes.windll.gdi32.DeleteDC(hdc_mem)
    ctypes.windll.user32.ReleaseDC(hwnd, hdc_win)

    return Image.frombuffer("RGBA", (ww, wh), buf, "raw", "BGRA", 0, 1).convert("RGB")


def capture_settings_tabs() -> None:
    """Open the simplified WebView2 Settings window and capture all 3 tabs."""
    import webview

    with tempfile.TemporaryDirectory() as td:
        cfg_path = Path(td) / "demo_config.json"
        cm = ConfigManager(cfg_path)
        cm.update(
            engine_mode="cloud",
            cloud_provider="groq",
            api_keys={
                "groq": "demo_groq_key_for_screenshot_display_only",
                "gemini": "demo_gemini_key",
                "anthropic": "demo_claude_key",
                "xai": "demo_grok_key",
                "nvidia": "demo_nvidia_key",
            },
            local_model_size="base",
            local_device="cpu",
            language="hinglish",
            hotkey_mode="hybrid",
            remove_fillers=True,
            smart_formatting=True,
            style_mode="clean",
            ui_sounds=True,
            sound_cues=True,
            custom_dictionary="",
        )
        cm.add_history(
            "um let us schedule the review tomorrow morning",
            "Let's schedule the review tomorrow morning.",
            "Offline",
            890,
        )
        cm.add_history(
            "अरे um भाई अपना काम जल्दी कर दो",
            "Bhai, apna kaam jaldi kar do.",
            "Groq • Hinglish",
            720,
        )

        win_title = "WinFlow"
        api = _SettingsJsApi(cm, initial_tab=0)
        window = webview.create_window(
            win_title,
            html=MINIMALIST_HTML,
            js_api=api,
            width=500,
            height=636,
            background_color="#0A0B0E",
        )

        tab_files = [
            (0, "settings-engine.png"),
            (1, "settings-cleanup.png"),
            (2, "settings-history.png"),
        ]

        def _capture_worker() -> None:
            try:
                _apply_dark_dwm_and_icon(win_title)
                time.sleep(0.95)
                hwnd = ctypes.windll.user32.FindWindowW(None, win_title)
                assert hwnd, "Could not find WebView2 HWND!"

                for idx, fname in tab_files:
                    window.evaluate_js(f"switchTab({idx}, true);")
                    time.sleep(0.45)
                    shot = _capture_hwnd_rgb(hwnd)
                    framed = Image.new("RGB", (shot.width + 48, shot.height + 48), (11, 13, 18))
                    framed.paste(shot, (24, 24))
                    framed.save(DOCS_IMG_DIR / fname, quality=95)
            finally:
                window.destroy()

        webview.start(_capture_worker, gui="edgechromium")


def capture_installer_showcase() -> None:
    """Capture both the License view and the single-bar Installing view of WinFlow-Setup.exe."""
    setup_exe = PROJECT_ROOT / "release_artifacts" / "WinFlow-Setup.exe"
    if not setup_exe.exists():
        return

    shots: list[Image.Image] = []
    for args in ([], ["--preview-progress=64"]):
        proc = subprocess.Popen([str(setup_exe), *args], cwd=str(PROJECT_ROOT))
        try:
            hwnd = 0
            for _ in range(30):
                time.sleep(0.15)
                hwnd = ctypes.windll.user32.FindWindowW(None, "WinFlow Setup")
                if hwnd:
                    break
            time.sleep(0.35)
            if hwnd:
                shots.append(_capture_hwnd_rgb(hwnd))
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except Exception:
                proc.kill()
            time.sleep(0.25)

    if len(shots) == 2:
        w1, h1 = shots[0].size
        w2, h2 = shots[1].size
        gap = 28
        pad = 28
        canvas = Image.new("RGB", (w1 + w2 + gap + pad * 2, max(h1, h2) + pad * 2), (11, 13, 18))
        canvas.paste(shots[0], (pad, pad))
        canvas.paste(shots[1], (pad + w1 + gap, pad))
        canvas.save(DOCS_IMG_DIR / "installer.png", quality=95)
        print("Saved docs/images/installer.png")


if __name__ == "__main__":
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_IMG_DIR.mkdir(parents=True, exist_ok=True)

    logo_512 = draw_logo_mark(512)
    logo_512.save(ASSETS_DIR / "logo.png")
    logo_512.save(
        ASSETS_DIR / "winflow.ico",
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("Saved assets/logo.png and assets/winflow.ico")

    build_hero_banner()
    print("Saved docs/images/hero-banner.png")

    build_floating_pills_showcase()
    print("Saved docs/images/floating-pills-showcase.png")

    capture_settings_tabs()
    print("Saved docs/images/settings-*.png")

    capture_installer_showcase()
