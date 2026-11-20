"""Dispatches one file to the right engine (Converter.swift)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import docs, ffmpeg_args as FF, images, pdfconv
from .formats import ConvSettings, OutGroup, Target
from .tools import ConvertError, Runner, Tool, unique_output


def convert(path: Path, t: Target, s: ConvSettings, runner: Runner,
            progress: Callable[[float], None] = lambda p: None) -> list[Path]:
    """Converts one file; returns what was written (files, or a folder for multi-page PDFs)."""
    g = t.group
    if g in (OutGroup.audio, OutGroup.video):
        ff = Tool.ffmpeg()
        if not ff:
            raise ConvertError("ffmpeg isn't installed. Install it with: winget install Gyan.FFmpeg")
        p = FF.probe(path)
        if p.ok and g == OutGroup.audio and not p.has_audio:
            raise ConvertError("This file has no audio track")
        if p.ok and g == OutGroup.video and not p.has_video:
            raise ConvertError("This file has no video track")
        return [_run_ffmpeg(ff, path, t, s, p, runner, progress)]
    if g == OutGroup.image:
        return [images.convert(path, t, s)]
    if g == OutGroup.document:
        return [docs.convert(path, t, s.paper_a4)]
    return pdfconv.convert(path, t, s, progress)


def _run_ffmpeg(ff, path, t, s, p, runner, progress) -> Path:
    def attempt(settings: ConvSettings) -> Path:
        out = unique_output(path, t.ext)
        codec = FF.audio_args(t, settings, p) if t.group == OutGroup.audio else FF.video_args(t, settings, p)
        try:
            runner.run(ff, FF.COMMON + ["-i", str(path)] + codec + [str(out)], p.duration, progress)
        except Exception:
            out.unlink(missing_ok=True)
            raise
        return out

    try:
        return attempt(s)
    except ConvertError:
        # A GPU encoder can fail at run time (driver, unsupported size). Retry once in software.
        if t.group == OutGroup.video and FF.uses_hardware(t, s) and not runner.cancelled:
            soft = ConvSettings.from_dict({**s.to_dict(), "hardware": False})
            return attempt(soft)
        raise
