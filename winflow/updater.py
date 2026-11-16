"""
Native GitHub Releases Auto-Updater for WinFlow.
Checks https://api.github.com/repos/piyushmaurya6/winflow/releases/latest in the background,
automatically downloads and SHA-256-verifies WinFlow-Setup.exe when a newer release exists,
and prompts the user to apply the update seamlessly via `WinFlow-Setup.exe --auto-update`.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import hashlib
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from . import __version__
from .config import UPDATES_DIR

log = logging.getLogger("winflow.updater")

GITHUB_REPO = "piyushmaurya6/winflow"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
INSTALLER_ASSET_NAME = "WinFlow-Setup.exe"

MB_YESNO = 0x00000004
MB_ICONINFORMATION = 0x00000040
MB_SETFOREGROUND = 0x00010000
MB_TOPMOST = 0x00040000
IDYES = 6

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
_user32.MessageBoxW.restype = ctypes.c_int

_SHA_BODY_RE = re.compile(
    r"WinFlow-Setup\.exe[`\s:*-]+`?([0-9a-fA-F]{64})`?",
    re.IGNORECASE,
)


@dataclass
class UpdateInfo:
    version: str
    tag_name: str
    download_url: str
    expected_size: int = 0
    expected_sha256: str = ""
    installer_path: Path | None = None
    html_url: str = ""


def parse_version(ver: str) -> tuple[int, ...]:
    """Parse 'v1.4.0' or '1.4.0' into a comparable integer tuple (1, 4, 0)."""
    cleaned = (ver or "").strip().lstrip("vV").split("-", 1)[0]
    parts: list[int] = []
    for seg in cleaned.split("."):
        m = re.match(r"(\d+)", seg.strip())
        if m:
            parts.append(int(m.group(1)))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer_version(candidate: str, current: str = __version__) -> bool:
    if not candidate or not candidate.strip():
        return False
    return parse_version(candidate) > parse_version(current)


def extract_release_update_info(
    release_json: dict[str, Any],
    current_version: str = __version__,
) -> UpdateInfo | None:
    """Return UpdateInfo if `release_json` is a newer non-draft release containing WinFlow-Setup.exe."""
    if not isinstance(release_json, dict):
        return None
    if release_json.get("draft") or release_json.get("prerelease"):
        return None

    tag_name = str(release_json.get("tag_name") or "").strip()
    if not is_newer_version(tag_name, current_version):
        return None

    assets = release_json.get("assets") or []
    chosen_asset: dict[str, Any] | None = None
    for asset in assets:
        if str(asset.get("name") or "").lower() == INSTALLER_ASSET_NAME.lower():
            chosen_asset = asset
            break
    if not chosen_asset:
        return None

    dl_url = str(chosen_asset.get("browser_download_url") or "").strip()
    if not dl_url:
        return None

    expected_size = int(chosen_asset.get("size") or 0)
    expected_sha = ""
    digest_val = str(chosen_asset.get("digest") or "").strip()
    if digest_val.lower().startswith("sha256:"):
        cand = digest_val.split(":", 1)[1].strip().lower()
        if len(cand) == 64:
            expected_sha = cand

    if not expected_sha:
        body = str(release_json.get("body") or "")
        m = _SHA_BODY_RE.search(body)
        if m:
            expected_sha = m.group(1).lower()

    clean_ver = ".".join(str(x) for x in parse_version(tag_name)[:3])
    return UpdateInfo(
        version=clean_ver,
        tag_name=tag_name if tag_name.startswith(("v", "V")) else f"v{clean_ver}",
        download_url=dl_url,
        expected_size=expected_size,
        expected_sha256=expected_sha,
        html_url=str(release_json.get("html_url") or ""),
    )


def verify_installer_file(
    file_path: Path,
    expected_size: int = 0,
    expected_sha256: str = "",
) -> bool:
    """Verify downloaded installer exists, has valid PE 'MZ' header, matches size, and matches SHA-256."""
    if not file_path.exists() or not file_path.is_file():
        return False
    actual_size = file_path.stat().st_size
    if actual_size < 4096:
        return False
    if expected_size > 0 and actual_size != expected_size:
        return False

    h = hashlib.sha256()
    with file_path.open("rb") as f:
        first_chunk = f.read(1024 * 1024)
        if len(first_chunk) < 2 or first_chunk[:2] != b"MZ":
            return False
        if not expected_sha256:
            return True
        h.update(first_chunk)
        while chunk := f.read(1024 * 1024):
            h.update(chunk)

    return h.hexdigest().lower() == expected_sha256.lower()


def prompt_user_to_install(info: UpdateInfo) -> bool:
    """Display a clean native Windows prompt asking whether to install the downloaded update now."""
    msg = (
        f"WinFlow {info.tag_name} has been downloaded and is ready to install.\n\n"
        "Update and restart WinFlow now?"
    )
    flags = MB_YESNO | MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND
    ans = _user32.MessageBoxW(None, msg, "WinFlow Update", flags)
    return ans == IDYES


def launch_update_installer(installer_path: Path) -> bool:
    """Launch downloaded WinFlow-Setup.exe in single-bar `--auto-update` mode."""
    if not installer_path or not installer_path.exists():
        return False
    try:
        subprocess.Popen(
            [str(installer_path), "--auto-update"],
            cwd=str(installer_path.parent),
            close_fds=True,
        )
        return True
    except Exception:
        log.exception("Failed to launch auto-update installer %s", installer_path)
        return False


class UpdateManager:
    """
    Background GitHub release checker & downloader.
    Automatically checks GitHub, downloads newer WinFlow-Setup.exe builds in the background,
    and invokes `on_update_ready` so WinFlow can prompt the user to update.
    """

    def __init__(
        self,
        current_version: str = __version__,
        api_url: str = LATEST_RELEASE_API,
        updates_dir: Path = UPDATES_DIR,
        on_update_ready: Callable[[UpdateInfo, bool], None] | None = None,
        on_status_toast: Callable[[str, bool], None] | None = None,
        min_auto_interval_s: float = 90.0,
    ) -> None:
        self.current_version = current_version
        self.api_url = api_url
        self.updates_dir = updates_dir
        self.on_update_ready = on_update_ready
        self.on_status_toast = on_status_toast
        self.min_auto_interval_s = min_auto_interval_s

        self._lock = threading.Lock()
        self._busy = False
        self._last_auto_check_ts = 0.0
        self.status: str = "idle"  # idle, checking, downloading, ready, up_to_date, error
        self.download_progress: float = 0.0
        self.ready_update: UpdateInfo | None = None
        self._prompted_tags: set[str] = set()

    def has_prompted(self, tag_name: str) -> bool:
        with self._lock:
            return tag_name in self._prompted_tags

    def mark_prompted(self, tag_name: str) -> None:
        with self._lock:
            self._prompted_tags.add(tag_name)

    def check_for_updates_async(self, manual: bool = False, delay_s: float = 0.0) -> None:
        with self._lock:
            if self._busy:
                return
            if not manual:
                if self.ready_update and self.ready_update.installer_path and self.ready_update.installer_path.exists():
                    info = self.ready_update
                    if info.tag_name not in self._prompted_tags and self.on_update_ready:
                        threading.Thread(
                            target=self.on_update_ready,
                            args=(info, False),
                            name="WinFlow-UpdateRePrompt",
                            daemon=True,
                        ).start()
                    return
                if (time.monotonic() - self._last_auto_check_ts) < self.min_auto_interval_s:
                    return
            self._busy = True

        threading.Thread(
            target=self._worker,
            args=(manual, delay_s),
            name="WinFlow-AutoUpdater",
            daemon=True,
        ).start()

    def check_and_download_sync(self, manual: bool = False) -> UpdateInfo | None:
        """Synchronous check + download helper (used by Settings UI manual check and tests)."""
        return self._perform_check_and_download(manual=manual)

    def _worker(self, manual: bool, delay_s: float) -> None:
        try:
            if delay_s > 0:
                time.sleep(delay_s)
            self._perform_check_and_download(manual=manual)
        finally:
            with self._lock:
                self._busy = False

    def _cleanup_old_installers(self, keep_path: Path) -> None:
        try:
            for item in self.updates_dir.glob("WinFlow-Setup-*.exe*"):
                if item.resolve() != keep_path.resolve():
                    try:
                        item.unlink()
                    except OSError:
                        pass
        except Exception:
            pass

    def _perform_check_and_download(self, manual: bool = False) -> UpdateInfo | None:
        with self._lock:
            if not manual:
                self._last_auto_check_ts = time.monotonic()
            self.status = "checking"

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"WinFlow/{self.current_version}",
        }

        try:
            with httpx.Client(timeout=12.0, follow_redirects=True, headers=headers) as client:
                resp = client.get(self.api_url)
                resp.raise_for_status()
                rel_json = resp.json()
        except Exception as exc:
            log.info("Update check skipped/offline: %s", exc)
            with self._lock:
                self.status = "error"
            if manual and self.on_status_toast:
                self.on_status_toast("Couldn't check updates", True)
            return None

        info = extract_release_update_info(rel_json, current_version=self.current_version)
        if info is None:
            with self._lock:
                self.status = "up_to_date"
            if manual and self.on_status_toast:
                self.on_status_toast(f"Up to date (v{self.current_version})", False)
            return None

        safe_tag = re.sub(r"[^A-Za-z0-9._-]", "", info.tag_name) or f"v{info.version}"
        self.updates_dir.mkdir(parents=True, exist_ok=True)
        final_path = self.updates_dir / f"WinFlow-Setup-{safe_tag}.exe"

        # Reuse already-downloaded verified installer if present
        if verify_installer_file(final_path, info.expected_size, info.expected_sha256):
            info.installer_path = final_path
            with self._lock:
                self.status = "ready"
                self.download_progress = 1.0
                self.ready_update = info
            log.info("Update %s already downloaded and verified at %s", info.tag_name, final_path)
            if self.on_update_ready:
                self.on_update_ready(info, manual)
            return info

        # Stream download in background
        part_path = self.updates_dir / f"WinFlow-Setup-{safe_tag}.exe.part.{os.getpid()}"
        with self._lock:
            self.status = "downloading"
            self.download_progress = 0.0

        if manual and self.on_status_toast:
            self.on_status_toast(f"Downloading {info.tag_name}…", False)

        log.info("Downloading update %s from %s", info.tag_name, info.download_url)
        try:
            hasher = hashlib.sha256()
            downloaded = 0
            with httpx.Client(
                timeout=httpx.Timeout(connect=12.0, read=120.0, write=30.0, pool=12.0),
                follow_redirects=True,
                headers={"User-Agent": f"WinFlow/{self.current_version}"},
            ) as dl_client:
                with dl_client.stream("GET", info.download_url) as stream:
                    stream.raise_for_status()
                    total = info.expected_size or int(stream.headers.get("Content-Length") or 0)
                    with part_path.open("wb") as out_f:
                        for chunk in stream.iter_bytes(chunk_size=262144):
                            if not chunk:
                                continue
                            out_f.write(chunk)
                            hasher.update(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                with self._lock:
                                    self.download_progress = min(0.99, downloaded / total)

            if info.expected_sha256 and hasher.hexdigest().lower() != info.expected_sha256.lower():
                log.error(
                    "SHA-256 mismatch for %s: expected %s, got %s",
                    info.tag_name,
                    info.expected_sha256,
                    hasher.hexdigest(),
                )
                part_path.unlink(missing_ok=True)
                with self._lock:
                    self.status = "error"
                if manual and self.on_status_toast:
                    self.on_status_toast("Update verification failed", True)
                return None

            if not verify_installer_file(part_path, info.expected_size, ""):
                log.error("Downloaded installer %s failed PE/size validation", part_path)
                part_path.unlink(missing_ok=True)
                with self._lock:
                    self.status = "error"
                if manual and self.on_status_toast:
                    self.on_status_toast("Update download incomplete", True)
                return None

            part_path.replace(final_path)
            self._cleanup_old_installers(final_path)
            info.installer_path = final_path

            with self._lock:
                self.status = "ready"
                self.download_progress = 1.0
                self.ready_update = info

            log.info("Update %s downloaded & verified (%d bytes)", info.tag_name, downloaded)
            if self.on_update_ready:
                self.on_update_ready(info, manual)
            return info

        except Exception as exc:
            log.warning("Failed downloading update %s: %s", info.tag_name, exc)
            try:
                part_path.unlink(missing_ok=True)
            except OSError:
                pass
            with self._lock:
                self.status = "error"
            if manual and self.on_status_toast:
                self.on_status_toast("Update download failed", True)
            return None
