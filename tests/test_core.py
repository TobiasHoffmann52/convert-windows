import subprocess
import json
import shutil
from pathlib import Path

import pytest

from convert_win import caps, ffmpeg_args as FF, tools
from convert_win.formats import ConvSettings, MediaKind, OutGroup, Targets
from convert_win.tools import Runner, Store, unique_folder, unique_output
from conftest import need


def test_kind_detection():
    k = MediaKind.of
    assert k("a.MP3") == MediaKind.audio and k("a.mkv") == MediaKind.video
    assert k("a.heic") == MediaKind.image and k("a.pdf") == MediaKind.pdf
    assert k("notes.md") == MediaKind.document and k("a.docx") == MediaKind.document
    assert k("a.xyz") is None and k("noext") is None


def test_video_can_become_audio_but_audio_cannot_become_video():
    assert {g for g, _ in Targets.groups({MediaKind.video})} == {OutGroup.video, OutGroup.audio}
    assert {g for g, _ in Targets.groups({MediaKind.audio})} == {OutGroup.audio}
    assert Targets.groups({MediaKind.audio, MediaKind.image}) == []
    assert {g for g, _ in Targets.groups({MediaKind.audio, MediaKind.video})} == {OutGroup.audio}


def test_preferred_skips_source_format():
    avail = [t for _, ts in Targets.groups({MediaKind.audio}) for t in ts]
    assert Targets.preferred({MediaKind.audio}, {"wav"}, avail).id == "mp3"
    assert Targets.preferred({MediaKind.audio}, {"mp3"}, avail).id == "wav"


def test_unique_names(tmp_path):
    f = tmp_path / "song.opus"
    f.write_bytes(b"x")
    assert unique_output(f, "mp3").name == "song.mp3"
    (tmp_path / "song.mp3").write_bytes(b"x")
    assert unique_output(f, "mp3").name == "song 2.mp3"
    (tmp_path / "song pages").mkdir()
    assert unique_folder(f, " pages").name == "song pages 2"


def test_settings_roundtrip_ignores_unknown_keys():
    s = ConvSettings(audio_bitrate=192, hardware=False)
    assert ConvSettings.from_dict({**s.to_dict(), "junk": 1}) == s


def test_store_persists(tmp_path):
    a = Store(tmp_path / "s.json"); a.set("k", {"x": 1})
    assert Store(tmp_path / "s.json").get("k") == {"x": 1}
    assert Store(tmp_path / "missing.json").get("k", 5) == 5


def test_tool_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("CONVERT_PANDOC", "none")
    tools.clear_cache()
    assert tools.Tool.pandoc() is None
    tools.clear_cache()


# ---------------------------------------------------------------- ffmpeg arguments (no ffmpeg needed)

def _t(i): return Targets.by_id(i)


def test_audio_args_choose_codec_and_cap_sample_rate():
    p = FF.Probe(sample_rate=96000, has_audio=True, ok=True)
    a = FF.audio_args(_t("m4a"), ConvSettings(audio_bitrate=256), p)
    assert a[a.index("-c:a") + 1] == "aac" and "256k" in a and a[a.index("-ar") + 1] == "48000"
    a = FF.audio_args(_t("opus"), ConvSettings(audio_bitrate=160), p)
    assert "-ar" not in a                                  # opus handles its own rates
    a = FF.audio_args(_t("mp3"), ConvSettings(), p)
    assert "-q:a" in a and "0" in a                        # VBR best


def test_lossless_depth_follows_source():
    hires = FF.Probe(hires=True, has_audio=True, ok=True)
    assert "pcm_s24le" in FF.audio_args(_t("wav"), ConvSettings(), hires)
    assert "pcm_s16le" in FF.audio_args(_t("wav"), ConvSettings(bit_depth=16), hires)


def test_video_args_software_and_filters(monkeypatch):
    monkeypatch.setattr(caps, "hw_encoder", lambda codec: None)
    p = FF.Probe(has_audio=True, has_video=True, sample_rate=44100, ok=True)
    v = FF.video_args(_t("mp4-h264"), ConvSettings(video_height=720, fps=30, video_quality=1.0), p)
    assert "libx264" in v and v[v.index("-crf") + 1] == "18"
    vf = v[v.index("-vf") + 1]
    assert "fps=30" in vf and "min(ih\\,720)" in vf
    assert "+faststart" in v
    assert "-an" in FF.video_args(_t("mp4-h264"), ConvSettings(keep_audio=False), p)


@pytest.mark.parametrize("enc,flag", [("h264_nvenc", "-cq"), ("h264_qsv", "-global_quality"), ("h264_amf", "-qp_i")])
def test_hardware_encoder_flags_per_vendor(monkeypatch, enc, flag):
    monkeypatch.setattr(caps, "hw_encoder", lambda codec: enc)
    v = FF.video_args(_t("mp4-h264"), ConvSettings(hardware=True), FF.Probe(has_audio=True, ok=True))
    assert enc in v and flag in v and "libx264" not in v
    v = FF.video_args(_t("mp4-h264"), ConvSettings(hardware=False), FF.Probe(has_audio=True, ok=True))
    assert "libx264" in v


def test_prores_never_uses_hardware(monkeypatch):
    monkeypatch.setattr(caps, "hw_encoder", lambda codec: "h264_nvenc")
    v = FF.video_args(_t("mov-prores"), ConvSettings(), FF.Probe(has_audio=True, ok=True))
    assert "prores_ks" in v and "pcm_s16le" in v


# ---------------------------------------------------------------- real ffmpeg

@need("ffmpeg")
def test_probe_and_runner_progress(tone, tmp_path):
    p = FF.probe(tone)
    assert p.ok and p.has_audio and not p.has_video and 0.9 < p.duration < 1.1
    seen = []
    out = tmp_path / "o.mp3"
    Runner().run(tools.Tool.ffmpeg(), FF.COMMON + ["-i", str(tone), "-c:a", "libmp3lame", str(out)], p.duration, seen.append)
    assert out.exists() and seen and max(seen) <= 1.0


@need("ffmpeg")
def test_runner_reports_ffmpeg_error(tmp_path):
    bad = tmp_path / "bad.mp3"; bad.write_bytes(b"nope")
    with pytest.raises(tools.ConvertError) as e:
        Runner().run(tools.Tool.ffmpeg(), FF.COMMON + ["-i", str(bad), str(tmp_path / "x.wav")])
    assert str(e.value)


@need("ffmpeg")
def test_cancel_stops_a_running_conversion(tmp_path):
    import threading, time
    r = Runner()
    err = []
    def go():
        try:
            r.run(tools.Tool.ffmpeg(), FF.COMMON + ["-re", "-f", "lavfi", "-i", "sine=duration=600", "-c:a", "flac", str(tmp_path / "long.flac")])
        except tools.ConvertError as e:
            err.append(str(e))
    th = threading.Thread(target=go); th.start(); time.sleep(0.7); r.cancel(); th.join(10)
    assert not th.is_alive() and err == ["Stopped"]


@need("ffmpeg")
@pytest.mark.parametrize("tid", ["mp3", "m4a", "wav", "aiff", "flac", "alac", "opus", "ogg", "wma", "ac3", "wv", "mp2"])
def test_every_audio_target_produces_the_right_codec(tone, tid):
    from convert_win import converter
    t = _t(tid)
    out = converter.convert(tone, t, ConvSettings(), Runner())[0]
    codec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    expected = {"mp3": "mp3", "m4a": "aac", "wav": "pcm_s16le", "aiff": "pcm_s16be", "flac": "flac", "alac": "alac",
                "opus": "opus", "ogg": "vorbis", "wma": "wmav2", "ac3": "ac3", "wv": "wavpack", "mp2": "mp2"}[tid]
    assert codec == expected


@need("ffmpeg")
def test_video_file_has_no_audio_track_error(tmp_path):
    from convert_win import converter
    v = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=d=1:s=160x120", str(v)], check=True)
    with pytest.raises(tools.ConvertError, match="no audio"):
        converter.convert(v, _t("mp3"), ConvSettings(), Runner())
