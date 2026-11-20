"""ffmpeg arguments for each audio/video format (FFmpeg.swift)."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import caps
from .formats import ConvSettings, Target
from .tools import NO_WINDOW, Tool

COMMON = ["-hide_banner", "-nostdin", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-y"]


@dataclass
class Probe:
    duration: float = 0.0
    sample_rate: int = 0
    hires: bool = False
    has_audio: bool = False
    has_video: bool = False
    ok: bool = False


def probe(path: Path) -> Probe:
    p = Probe()
    fp = Tool.ffprobe()
    if not fp:
        return p
    try:
        r = subprocess.run([fp, "-v", "error", "-show_entries",
                            "format=duration:stream=codec_type,sample_rate,sample_fmt,bits_per_raw_sample,disposition",
                            "-of", "json", str(path)], capture_output=True, timeout=60,
                           stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        if r.returncode != 0:
            return p
        j = json.loads(r.stdout.decode("utf-8", "replace"))
    except Exception:
        return p
    p.ok = True
    try:
        p.duration = float(j.get("format", {}).get("duration", 0) or 0)
    except ValueError:
        pass
    streams = j.get("streams", [])
    p.has_video = any(s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic", 0)
                      for s in streams)                      # cover art doesn't count
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if a:
        p.has_audio = True
        p.sample_rate = int(a.get("sample_rate") or 0)
        bits = int(a.get("bits_per_raw_sample") or 0)
        p.hires = str(a.get("sample_fmt", "")).startswith("s32") or bits > 16
    return p


def _vorbis_quality(kbps: int) -> str:
    for limit, q in ((320, "9"), (256, "8"), (192, "6"), (160, "5"), (128, "4")):
        if kbps >= limit:
            return q
    return "2"


def audio_args(t: Target, s: ConvSettings, p: Probe) -> list[str]:
    a = ["-map", "0:a:0", "-vn", "-map_metadata", "0", "-map_metadata", "0:s:a:0"]
    br = s.audio_bitrate if s.audio_bitrate in t.bitrate_options else t.default_bitrate
    deep = s.bit_depth == 24 or (s.bit_depth == 0 and p.hires)
    cap48 = t.is_lossy_audio and t.id not in ("opus", "mp3")
    i = t.id
    if i == "mp3":
        a += ["-c:a", "libmp3lame"] + (["-q:a", "0"] if br == 0 else ["-b:a", f"{br}k"]); cap48 = True
    elif i == "m4a":     a += ["-c:a", "aac", "-b:a", f"{br}k"]
    elif i == "he-aac":  a += ["-c:a", "libfdk_aac", "-profile:a", "aac_he", "-b:a", f"{br}k"]
    elif i == "opus":    a += ["-c:a", "libopus", "-b:a", f"{br}k"]
    elif i == "ogg":
        if caps.has_encoder("libvorbis"):
            a += ["-c:a", "libvorbis", "-q:a", _vorbis_quality(br)]
        else:
            a += ["-c:a", "vorbis", "-strict", "experimental", "-ac", "2", "-b:a", f"{br}k"]
    elif i == "wma":     a += ["-c:a", "wmav2", "-b:a", f"{br}k"]
    elif i == "ac3":     a += ["-c:a", "ac3", "-b:a", f"{br}k"]
    elif i == "mp2":     a += ["-c:a", "mp2", "-b:a", f"{br}k"]
    elif i == "wav":     a += ["-c:a", "pcm_s24le" if deep else "pcm_s16le"]
    elif i == "aiff":    a += ["-c:a", "pcm_s24be" if deep else "pcm_s16be"]
    elif i == "flac":    a += ["-c:a", "flac", "-sample_fmt", "s32" if deep else "s16"]
    elif i == "alac":    a += ["-c:a", "alac", "-sample_fmt", "s32p" if deep else "s16p"]
    elif i == "wv":      a += ["-c:a", "wavpack", "-sample_fmt", "s32p" if deep else "s16p"]
    if s.sample_rate > 0 and s.sample_rate in t.sample_rates:
        a += ["-ar", str(s.sample_rate)]
    elif cap48 and p.sample_rate > 48000:
        a += ["-ar", "48000"]
    if s.channels > 0 and t.has_channel_option:
        a += ["-ac", str(s.channels)]
    return a


def _hw_video(encoder: str, crf: int, codec: str) -> list[str]:
    """Quality flags for each GPU vendor. `crf` is the equivalent software CRF (lower = better)."""
    if encoder.endswith("_nvenc"):
        return ["-c:v", encoder, "-preset", "p5", "-rc", "vbr", "-cq", str(crf), "-b:v", "0"]
    if encoder.endswith("_qsv"):
        return ["-c:v", encoder, "-preset", "medium", "-global_quality", str(crf)]
    # AMD AMF
    return ["-c:v", encoder, "-quality", "quality", "-rc", "cqp",
            "-qp_i", str(crf), "-qp_p", str(crf + 2), "-qp_b", str(crf + 4)]


def uses_hardware(t: Target, s: ConvSettings) -> bool:
    if not (s.hardware and t.has_hardware_option):
        return False
    return bool(caps.hw_encoder("hevc" if t.id == "mp4-hevc" else "h264"))


def video_args(t: Target, s: ConvSettings, p: Probe) -> list[str]:
    q = max(0.0, min(1.0, s.video_quality))
    filters: list[str] = []
    v = ["-map", "0:v:0", "-map_metadata", "0"]

    if t.id == "gif":
        vf = (f"fps={s.gif_fps},scale=min(iw\\,{s.gif_width}):-2:flags=lanczos,split[a][b];"
              "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a")
        return v + ["-vf", vf, "-loop", "0", "-an"]

    if s.fps > 0:
        filters.append(f"fps={s.fps}")
    if s.video_height > 0:
        filters.append(f"scale=-2:min(ih\\,{s.video_height})")

    audio = ["-c:a", "aac", "-b:a", "192k"]
    hw = uses_hardware(t, s)
    if t.id in ("mp4-h264", "mkv"):
        crf = round(30 - q * 12)
        if hw:
            v += _hw_video(caps.hw_encoder("h264"), crf, "h264") + ["-pix_fmt", "yuv420p"]
        else:
            v += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p"]
    elif t.id == "mp4-hevc":
        crf = round(32 - q * 12)
        if hw:
            v += _hw_video(caps.hw_encoder("hevc"), crf, "hevc")
        else:
            v += ["-c:v", "libx265", "-preset", "medium", "-crf", str(crf), "-x265-params", "log-level=error"]
        v += ["-tag:v", "hvc1"]
    elif t.id == "mov-prores":
        v += ["-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le"]
        audio = ["-c:a", "pcm_s16le"]
    elif t.id == "webm":
        v += ["-c:v", "libvpx-vp9", "-crf", str(round(42 - q * 17)), "-b:v", "0",
              "-row-mt", "1", "-deadline", "good", "-cpu-used", "4"]
        audio = ["-c:a", "libopus", "-b:a", "128k"]
    elif t.id == "mp4-av1":
        enc = caps.av1_encoder()
        if enc == "libsvtav1":
            v += ["-c:v", "libsvtav1", "-crf", str(round(46 - q * 20)), "-preset", "8"]
        else:
            v += ["-c:v", "libaom-av1", "-crf", str(round(46 - q * 20)), "-b:v", "0", "-cpu-used", "6",
                  "-row-mt", "1"]
    if filters:
        v += ["-vf", ",".join(filters)]

    if s.keep_audio and p.has_audio:
        v += ["-map", "0:a:0"] + audio
        if audio[1] == "aac" and p.sample_rate > 48000:
            v += ["-ar", "48000"]
    else:
        v += ["-an"]
    if t.ext in ("mp4", "mov"):
        v += ["-movflags", "+faststart"]
    return v
