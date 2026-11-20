"""Finding external tools, running processes, naming output files (Process.swift)."""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

IS_WINDOWS = sys.platform == "win32"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


class ConvertError(Exception):
    pass


def app_dir() -> Path:
    """Folder of the exe (frozen) or the package (source)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    base = os.environ.get("APPDATA") if IS_WINDOWS else None
    p = Path(base) / "Convert" if base else Path.home() / ".config" / "Convert"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _env_dirs(*names_and_subs: tuple[str, str]) -> list[str]:
    out = []
    for var, sub in names_and_subs:
        v = os.environ.get(var)
        if v:
            out.append(os.path.join(v, sub) if sub else v)
    return out


def _candidate_dirs(name: str) -> list[str]:
    """Places Windows tools live that may not be on PATH.

    Explorer starts right-click launches with the PATH it had at sign-in, so a tool
    installed later (winget, scoop…) is often invisible to PATH until the next sign-in.
    """
    dirs = [str(app_dir() / "tools"), str(app_dir())]
    if not IS_WINDOWS:
        return dirs
    local = os.environ.get("LOCALAPPDATA", "")
    home = str(Path.home())
    dirs += _env_dirs(("LOCALAPPDATA", r"Microsoft\WindowsApps"), ("LOCALAPPDATA", r"Microsoft\WinGet\Links"), ("ProgramFiles", "ffmpeg/bin"),
                      ("ChocolateyInstall", "bin"), ("USERPROFILE", r"scoop\shims"))
    dirs += ["C:/ffmpeg/bin", "C:/ProgramData/chocolatey/bin"]
    dirs += glob.glob(os.path.join(local, r"Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin"))
    dirs += glob.glob(os.path.join(local, r"Microsoft\WinGet\Packages\*FFmpeg*\**\bin"), recursive=True)
    dirs += glob.glob(os.path.join(home, r"scoop\apps\ffmpeg\current\bin"))
    dirs += _env_dirs(("ProgramFiles", r"LibreOffice\program"), ("ProgramFiles(x86)", r"LibreOffice\program"),
                      ("LOCALAPPDATA", "Pandoc"), ("ProgramFiles", "Pandoc"),
                      ("ProgramFiles", r"Microsoft\Edge\Application"), ("ProgramFiles(x86)", r"Microsoft\Edge\Application"),
                      ("ProgramFiles", r"Google\Chrome\Application"), ("ProgramFiles(x86)", r"Google\Chrome\Application"))
    return dirs


_cache: dict[str, Optional[str]] = {}


def find(name: str, env_override: str | None = None) -> Optional[str]:
    """Locate an executable by name. `env_override` is an environment variable holding a full path."""
    key = name + "|" + (env_override or "")
    if key in _cache:
        return _cache[key]
    found: Optional[str] = None
    if env_override and os.environ.get(env_override, "x").lower() in ("", "none", "off", "0"):
        _cache[key] = None            # e.g. CONVERT_SOFFICE=none turns that tool off
        return None
    if env_override and os.environ.get(env_override):
        p = os.environ[env_override]
        found = p if os.path.isfile(p) else None
    if not found:
        found = shutil.which(name)
    if not found:
        for d in _candidate_dirs(name):
            for ext in ([""] if "." in name else [".exe"] if IS_WINDOWS else [""]):
                p = os.path.join(d, name + ext)
                if os.path.isfile(p):
                    found = p
                    break
            if found:
                break
    _cache[key] = found
    return found


class Tool:
    """Lazy lookups so tests can monkeypatch environment variables first."""
    @staticmethod
    def ffmpeg(): return find("ffmpeg", "CONVERT_FFMPEG")
    @staticmethod
    def ffprobe(): return find("ffprobe", "CONVERT_FFPROBE")
    @staticmethod
    def soffice():
        # soffice.com is the console launcher that waits for the conversion to finish; soffice.exe may not.
        return find("soffice", "CONVERT_SOFFICE") if not IS_WINDOWS else (
            find("soffice.com", "CONVERT_SOFFICE") or find("soffice", "CONVERT_SOFFICE"))
    @staticmethod
    def pandoc(): return find("pandoc", "CONVERT_PANDOC")
    @staticmethod
    def winget(): return find("winget", "CONVERT_WINGET")
    @staticmethod
    def browser():
        if os.environ.get("CONVERT_BROWSER"):
            return find("browser", "CONVERT_BROWSER")
        return find("msedge") or find("chrome") or find("chromium") or find("google-chrome")


def clear_cache() -> None:
    _cache.clear()


def last_line(text: str | bytes) -> Optional[str]:
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return lines[-1] if lines else None


def run_sync(exe: str, args: list[str], timeout: float | None = None, env: dict | None = None) -> str:
    """Run a tool to completion and return stdout. Raises with the last stderr line on failure."""
    try:
        p = subprocess.run([exe, *args], capture_output=True, stdin=subprocess.DEVNULL,
                           timeout=timeout, creationflags=NO_WINDOW, env=env)
    except subprocess.TimeoutExpired:
        raise ConvertError(f"{Path(exe).name} took too long and was stopped")
    if p.returncode != 0:
        raise ConvertError(last_line(p.stderr) or last_line(p.stdout) or f"{Path(exe).name} failed")
    return p.stdout.decode("utf-8", "replace")


def unique_output(src: Path, ext: str, suffix: str = "") -> Path:
    """Next free name next to the input: 'name.ext', then 'name 2.ext', …"""
    base = src.stem + suffix
    out = src.with_name(f"{base}.{ext}")
    n = 2
    while out.exists():
        out = src.with_name(f"{base} {n}.{ext}")
        n += 1
    return out


def unique_folder(src: Path, suffix: str) -> Path:
    base = src.stem + suffix
    out = src.with_name(base)
    n = 2
    while out.exists():
        out = src.with_name(f"{base} {n}")
        n += 1
    return out


class Runner:
    """Runs ffmpeg-style processes with live progress and cancellation."""

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self.cancelled = False

    def cancel(self) -> None:
        with self._lock:
            self.cancelled = True
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()

    def run(self, exe: str, args: list[str], duration: float = 0.0,
            progress: Callable[[float], None] = lambda p: None) -> None:
        with self._lock:
            if self.cancelled:
                raise ConvertError("Stopped")
            self._proc = subprocess.Popen(
                [exe, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=NO_WINDOW)
            proc = self._proc
        err_chunks: list[bytes] = []
        t = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()), daemon=True)
        t.start()
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if duration > 0 and line.startswith("out_time_us="):
                try:
                    us = float(line.split("=", 1)[1])
                except ValueError:
                    continue
                if us >= 0:
                    progress(min(1.0, us / 1_000_000 / duration))
        code = proc.wait()
        t.join(timeout=2)
        with self._lock:
            self._proc = None
            was_cancelled = self.cancelled
        if code != 0:
            if was_cancelled:
                raise ConvertError("Stopped")
            raise ConvertError(last_line(b"".join(err_chunks)) or f"{Path(exe).name} exited with code {code}")


class Store:
    """Tiny JSON key/value store replacing UserDefaults."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "settings.json")
        try:
            self.data = json.loads(self.path.read_text("utf-8"))
        except Exception:
            self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data), "utf-8")
        except OSError:
            pass
