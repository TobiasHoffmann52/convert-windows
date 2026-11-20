"""File kinds, output formats and per-format options (Formats.swift)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

from . import caps
from .tools import Tool

# --------------------------------------------------------------------- input kinds

VIDEO_EXTS = {"mp4", "m4v", "mov", "mkv", "webm", "avi", "flv", "wmv", "ts", "mts", "m2ts", "3gp", "ogv",
              "mxf", "vob", "mpg", "mpeg", "mpe"}
AUDIO_EXTS = {"mp3", "wav", "aif", "aiff", "aifc", "flac", "m4a", "aac", "ogg", "oga", "opus", "wma", "ac3",
              "dts", "amr", "mp2", "mka", "ape", "wv", "tta", "dsf", "dff", "caf"}
IMAGE_EXTS = {"jpg", "jpeg", "jpe", "jfif", "png", "gif", "bmp", "tif", "tiff", "webp", "heic", "heif", "avif",
              "ico"}
RAW_EXTS = {"dng", "cr2", "cr3", "nef", "arw", "orf", "rw2", "raf", "srw", "pef"}
MARKDOWN_EXTS = {"md", "markdown", "mdown", "mkd"}
DOC_EXTS = {"doc", "docx", "odt", "rtf", "html", "htm", "txt", "text"} | MARKDOWN_EXTS
PDF_EXTS = {"pdf"}

ALL_INPUT_EXTS = sorted(VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS | RAW_EXTS | DOC_EXTS | PDF_EXTS)


def ext_of(path: Path | str) -> str:
    return Path(path).suffix.lower().lstrip(".")


class MediaKind(str, Enum):
    audio = "audio"
    video = "video"
    image = "image"
    pdf = "pdf"
    document = "document"

    @staticmethod
    def of(path: Path | str) -> Optional["MediaKind"]:
        e = ext_of(path)
        if e in VIDEO_EXTS: return MediaKind.video
        if e in AUDIO_EXTS: return MediaKind.audio
        if e in IMAGE_EXTS or e in RAW_EXTS: return MediaKind.image
        if e in PDF_EXTS: return MediaKind.pdf
        if e in DOC_EXTS: return MediaKind.document
        return None

    @property
    def output_groups(self) -> set["OutGroup"]:
        return {
            MediaKind.audio: {OutGroup.audio},
            MediaKind.video: {OutGroup.video, OutGroup.audio},
            MediaKind.image: {OutGroup.image},
            MediaKind.pdf: {OutGroup.pdf_pages},
            MediaKind.document: {OutGroup.document},
        }[self]

    @property
    def noun(self) -> str:
        return {"audio": "Audio", "video": "Video", "image": "Image", "pdf": "PDF", "document": "Document"}[self.value]


class OutGroup(str, Enum):
    video = "Video"
    audio = "Audio"
    image = "Image"
    document = "Document"
    pdf_pages = "From PDF"


GROUP_ORDER = [OutGroup.video, OutGroup.audio, OutGroup.image, OutGroup.document, OutGroup.pdf_pages]


@dataclass(frozen=True)
class Target:
    id: str
    label: str
    ext: str
    group: OutGroup

    # ---- per-target options
    @property
    def is_lossy_audio(self): return self.id in {"mp3", "m4a", "he-aac", "opus", "ogg", "wma", "ac3", "mp2"}
    @property
    def is_lossless_audio(self): return self.id in {"wav", "aiff", "flac", "alac", "wv"}
    @property
    def is_lossy_image(self): return self.id in {"jpg", "heic", "avif", "webp"}
    @property
    def has_hardware_option(self): return self.id in {"mp4-h264", "mp4-hevc", "mkv"}

    @property
    def bitrate_options(self) -> list[int]:
        return {
            "mp3": [0, 320, 256, 192, 160, 128, 96, 64], "m4a": [320, 256, 192, 160, 128, 96],
            "he-aac": [96, 80, 64, 48, 32], "opus": [256, 192, 160, 128, 96, 64, 48, 32],
            "ogg": [320, 256, 192, 160, 128, 96], "wma": [256, 192, 160, 128, 96],
            "ac3": [640, 448, 384, 256, 192], "mp2": [384, 320, 256, 192, 128],
        }.get(self.id, [])

    @property
    def default_bitrate(self) -> int:
        return {"m4a": 256, "he-aac": 64, "opus": 160, "ogg": 192, "wma": 192, "ac3": 448, "mp2": 256}.get(self.id, 0)

    @property
    def sample_rates(self) -> list[int]:
        if self.id == "opus":
            return []
        return [0, 44100, 48000] if self.is_lossy_audio else [0, 44100, 48000, 88200, 96000]

    @property
    def has_channel_option(self) -> bool:
        # The built-in Vorbis encoder is stereo-only; libvorbis isn't.
        return not (self.id == "ogg" and not caps.has_encoder("libvorbis"))

    @property
    def has_settings(self) -> bool:
        return not (self.group == OutGroup.document and self.id != "pdf-doc") and self.id != "pdf-txt"


def _t(group: OutGroup, rows: Iterable[tuple[str, str, str]]) -> list[Target]:
    return [Target(i, l, e, group) for i, l, e in rows]


_targets_cache: list[Target] | None = None


class Targets:
    @staticmethod
    def all() -> list[Target]:
        global _targets_cache
        if _targets_cache is not None:
            return _targets_cache
        can = caps.has_encoder
        video = [("mp4-h264", "MP4 (H.264)", "mp4"), ("mp4-hevc", "MP4 (HEVC / H.265)", "mp4"),
                 ("mov-prores", "MOV (ProRes 422)", "mov"), ("webm", "WebM (VP9)", "webm"),
                 ("mkv", "MKV (H.264)", "mkv")]
        if caps.av1_encoder():
            video.append(("mp4-av1", "MP4 (AV1)", "mp4"))
        video.append(("gif", "GIF (animated)", "gif"))
        audio = [("mp3", "MP3", "mp3"), ("m4a", "M4A (AAC)", "m4a")]
        if can("libfdk_aac"):
            audio.append(("he-aac", "M4A (HE-AAC, small)", "m4a"))
        audio += [("wav", "WAV", "wav"), ("aiff", "AIFF", "aiff"), ("flac", "FLAC", "flac"),
                  ("alac", "ALAC (Apple Lossless)", "m4a"), ("opus", "Opus", "opus"), ("ogg", "OGG (Vorbis)", "ogg"),
                  ("wma", "WMA", "wma"), ("ac3", "AC3 (Dolby Digital)", "ac3"), ("wv", "WavPack", "wv"),
                  ("mp2", "MP2", "mp2")]
        image = [("jpg", "JPEG", "jpg"), ("png", "PNG", "png")]
        if caps.pillow_can_write("HEIF"): image.append(("heic", "HEIC", "heic"))
        if caps.pillow_can_write("AVIF"): image.append(("avif", "AVIF", "avif"))
        if caps.pillow_can_write("WEBP"): image.append(("webp", "WebP", "webp"))
        image += [("tiff", "TIFF", "tiff"), ("gif-img", "GIF", "gif"), ("bmp", "BMP", "bmp"), ("pdf-img", "PDF", "pdf")]
        doc = [("pdf-doc", "PDF", "pdf"), ("docx", "Word (DOCX)", "docx"), ("doc", "Word 97–2004 (DOC)", "doc"),
               ("odt", "OpenDocument (ODT)", "odt"), ("rtf", "Rich Text (RTF)", "rtf"),
               ("txt", "Plain Text", "txt"), ("html", "HTML", "html")]
        pdf = [("pdf-png", "PNG (one per page)", "png"), ("pdf-jpg", "JPEG (one per page)", "jpg"),
               ("pdf-txt", "Plain Text", "txt")]
        _targets_cache = (_t(OutGroup.video, video) + _t(OutGroup.audio, audio) + _t(OutGroup.image, image)
                          + _t(OutGroup.document, doc) + _t(OutGroup.pdf_pages, pdf))
        return _targets_cache

    @staticmethod
    def by_id(i: str) -> Optional[Target]:
        return next((t for t in Targets.all() if t.id == i), None)

    @staticmethod
    def groups(kinds: set[MediaKind]) -> list[tuple[OutGroup, list[Target]]]:
        if not kinds:
            return []
        allowed = set(OutGroup)
        for k in kinds:
            allowed &= k.output_groups
        return [(g, [t for t in Targets.all() if t.group == g]) for g in GROUP_ORDER if g in allowed]

    @staticmethod
    def preferred(kinds: set[MediaKind], source_exts: set[str], available: list[Target]) -> Optional[Target]:
        if len(kinds) == 1:
            k = next(iter(kinds))
        elif kinds == {MediaKind.audio, MediaKind.video}:
            k = MediaKind.audio
        else:
            k = None
        order = {MediaKind.audio: ["mp3", "wav", "m4a"], MediaKind.video: ["mp4-h264", "mov-prores"],
                 MediaKind.image: ["jpg", "png"], MediaKind.pdf: ["pdf-png"],
                 MediaKind.document: ["pdf-doc", "docx"]}.get(k, [])
        for i in order:
            t = next((x for x in available if x.id == i), None)
            if t and t.ext not in source_exts:
                return t
        return next((t for t in available if t.ext not in source_exts), available[0] if available else None)


@dataclass
class ConvSettings:
    audio_bitrate: int = 0          # kbps; 0 = MP3 VBR best
    sample_rate: int = 0            # 0 = same as source
    channels: int = 0               # 0 = same, 1 mono, 2 stereo
    bit_depth: int = 0              # 0 = match source, 16, 24
    video_quality: float = 0.65     # 0 smallest … 1 best
    video_height: int = 0
    fps: int = 0
    keep_audio: bool = True
    hardware: bool = True
    gif_width: int = 480
    gif_fps: int = 15
    image_quality: float = 0.85
    max_size: int = 0
    paper_a4: bool = True
    pdf_dpi: int = 150

    def to_dict(self) -> dict: return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "ConvSettings":
        names = {f.name for f in fields(ConvSettings)}
        return ConvSettings(**{k: v for k, v in d.items() if k in names})
