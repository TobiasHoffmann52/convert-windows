"""Image conversion with Pillow (ImageConv.swift used Apple's ImageIO)."""
from __future__ import annotations

from pathlib import Path

from .formats import ConvSettings, Target, ext_of, RAW_EXTS
from .tools import ConvertError, unique_output

_registered = False


def register_plugins() -> None:
    global _registered
    if _registered:
        return
    _registered = True
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()      # HEIC/HEIF read + write
    except Exception:
        pass
    try:
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None            # the user's own files, not untrusted web input
    except Exception:
        pass


# Pillow format name per target id
PIL_FORMAT = {"jpg": "JPEG", "png": "PNG", "heic": "HEIF", "avif": "AVIF", "webp": "WEBP", "tiff": "TIFF",
              "gif-img": "GIF", "bmp": "BMP", "pdf-img": "PDF", "pdf-jpg": "JPEG", "pdf-png": "PNG"}
_OPAQUE = {"jpg", "bmp", "pdf-img", "pdf-jpg"}      # no alpha channel: flatten onto white
_KEEPS_META = {"JPEG", "PNG", "WEBP", "HEIF", "AVIF", "TIFF"}


def _load(path: Path):
    from PIL import Image
    if ext_of(path) in RAW_EXTS:
        try:
            import rawpy
        except ImportError:
            raise ConvertError("RAW photos need the optional 'rawpy' package: pip install rawpy")
        with rawpy.imread(str(path)) as raw:
            return Image.fromarray(raw.postprocess(use_camera_wb=True))
    try:
        img = Image.open(path)
        img.seek(0)                    # first frame of animated GIF / multi-image HEIC only
        img.load()
        return img
    except Exception as e:
        raise ConvertError(f"Couldn't read this image ({e.__class__.__name__})")


def _normalise(img, opaque: bool):
    from PIL import Image
    if img.mode in ("I;16", "I;16L", "I;16B", "I", "F"):
        img = img.point(lambda v: v * (1 / 256)).convert("L") if img.mode != "F" else img.convert("L")
    if img.mode in ("P", "PA", "LA", "La", "RGBa"):
        img = img.convert("RGBA")
    if img.mode == "CMYK":
        img = img.convert("RGB")
    if opaque and img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.getchannel("A"))
        return bg
    if opaque and img.mode != "RGB":
        return img.convert("RGB")
    return img


def convert(path: Path, t: Target, s: ConvSettings) -> Path:
    from PIL import Image, ImageOps
    register_plugins()
    fmt = PIL_FORMAT.get(t.id)
    if not fmt:
        raise ConvertError(f"Unsupported image format {t.label}")
    src = _load(path)
    try:
        exif = src.getexif()
        icc = src.info.get("icc_profile")
        dpi = src.info.get("dpi")
        img = ImageOps.exif_transpose(src)         # bake in rotation, like the Mac version
        if s.max_size > 0 and max(img.size) > s.max_size:
            img = img.copy()
            img.thumbnail((s.max_size, s.max_size), Image.LANCZOS)
        if img.mode == "RGBA" and fmt == "JPEG" or t.id in _OPAQUE:
            img = _normalise(img, True)
        else:
            img = _normalise(img, False)
        if fmt == "GIF" and img.mode == "RGBA":
            pass                                    # Pillow quantises and keeps transparency
        kw: dict = {}
        if fmt in _KEEPS_META:
            if exif:
                exif[0x0112] = 1                    # orientation already applied
                kw["exif"] = exif.tobytes()
            if icc and img.mode in ("RGB", "RGBA", "L", "CMYK"):
                kw["icc_profile"] = icc
        if t.is_lossy_image:
            kw["quality"] = max(1, min(100, round(s.image_quality * 100)))
        if fmt == "JPEG":
            kw["optimize"] = True
        if fmt == "PDF":
            ok = dpi and 36 <= float(dpi[0]) <= 1200
            kw["resolution"] = float(dpi[0]) if ok else 150.0
        out = unique_output(path, t.ext)
        try:
            img.save(out, format=fmt, **kw)
        except Exception as e:
            out.unlink(missing_ok=True)
            raise ConvertError(f"Couldn't write {t.label} ({e})")
        return out
    finally:
        src.close()          # release the file handle so the original can be recycled on Windows
