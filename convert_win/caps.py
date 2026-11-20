"""What this machine's ffmpeg / Pillow can actually do."""
from __future__ import annotations

import functools
import subprocess

from .tools import NO_WINDOW, Tool


@functools.lru_cache(maxsize=1)
def encoders() -> frozenset[str]:
    ff = Tool.ffmpeg()
    if not ff:
        return frozenset()
    try:
        out = subprocess.run([ff, "-hide_banner", "-encoders"], capture_output=True, timeout=20,
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW).stdout.decode("utf-8", "replace")
    except Exception:
        return frozenset()
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)


def has_encoder(name: str) -> bool:
    return name in encoders()


# Hardware H.264/HEVC encoders, best first. NVIDIA, Intel Quick Sync, AMD.
_HW_BACKENDS = ("nvenc", "qsv", "amf")
_hw_cache: dict[str, str | None] = {}


def _hw_works(encoder: str) -> bool:
    """Compile-time presence isn't enough (no GPU = failure), so try a tiny real encode."""
    ff = Tool.ffmpeg()
    if not ff or not has_encoder(encoder):
        return False
    try:
        p = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=black:s=640x360:d=0.2:r=25", "-frames:v", "5", "-c:v", encoder,
                            "-f", "null", "-"], capture_output=True, timeout=30,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        return p.returncode == 0
    except Exception:
        return False


def hw_encoder(codec: str) -> str | None:
    """Working GPU encoder name for 'h264' or 'hevc', or None."""
    if codec in _hw_cache:
        return _hw_cache[codec]
    found = None
    for b in _HW_BACKENDS:
        name = f"{codec}_{b}"
        if _hw_works(name):
            found = name
            break
    _hw_cache[codec] = found
    return found


def hw_available() -> bool:
    return bool(hw_encoder("h264") or hw_encoder("hevc"))


def av1_encoder() -> str | None:
    for e in ("libsvtav1", "libaom-av1"):
        if has_encoder(e):
            return e
    return None


def pillow_can_write(fmt: str) -> bool:
    from PIL import Image
    from . import images  # registers HEIF opener
    images.register_plugins()
    Image.init()
    return fmt in Image.SAVE
