import shutil
import pytest
from PIL import Image, ImageDraw

from convert_win import converter, pdfconv, tools
from convert_win.formats import ConvSettings, Targets
from convert_win.tools import ConvertError, Runner
from conftest import need


def T(i): return Targets.by_id(i)


def conv(path, tid, **s):
    return converter.convert(path, T(tid), ConvSettings(**s), Runner())


@pytest.fixture
def photo(tmp_path):
    im = Image.new("RGB", (400, 300), (30, 120, 200))
    ImageDraw.Draw(im).rectangle([10, 10, 100, 60], fill=(255, 255, 0))
    ex = Image.Exif(); ex[0x0112] = 6; ex[0x010F] = "TestCam"
    p = tmp_path / "photo.jpg"; im.save(p, exif=ex.tobytes()); return p


@pytest.fixture
def logo(tmp_path):
    im = Image.new("RGBA", (100, 100), (255, 0, 0, 0))
    ImageDraw.Draw(im).ellipse([10, 10, 90, 90], fill=(0, 128, 255, 255))
    p = tmp_path / "logo.png"; im.save(p); return p


@pytest.mark.parametrize("tid", ["jpg", "png", "heic", "avif", "webp", "tiff", "gif-img", "bmp", "pdf-img"])
def test_every_image_target_writes_a_readable_file(photo, tid):
    if T(tid) is None:
        pytest.skip(f"{tid} not supported by this Pillow build")
    out = conv(photo, tid)[0]
    assert out.exists() and out.stat().st_size > 0


def test_exif_rotation_is_applied_and_metadata_kept(photo):
    out = conv(photo, "png")[0]
    im = Image.open(out)
    assert im.size == (300, 400)                       # 400x300 rotated by orientation=6
    assert im.getexif().get(0x010F) == "TestCam" and im.getexif().get(0x0112) == 1
    im.close()


def test_transparency_flattened_to_white_for_jpeg_but_kept_for_png_webp(logo):
    j = Image.open(conv(logo, "jpg")[0]); assert j.mode == "RGB" and j.getpixel((1, 1)) == (255, 255, 255); j.close()
    p = Image.open(conv(logo, "webp")[0]); assert p.mode == "RGBA" and p.convert("RGBA").getpixel((1, 1))[3] == 0; p.close()


def test_max_size_never_upscales(photo):
    small = Image.open(conv(photo, "png", max_size=100)[0]); assert max(small.size) == 100; small.close()
    same = Image.open(conv(photo, "png", max_size=4096)[0]); assert same.size == (300, 400); same.close()


def test_original_file_is_not_left_locked(photo):
    conv(photo, "png")
    photo.unlink()                                      # would fail on Windows if a handle stayed open


def test_unreadable_image_gives_clean_error(tmp_path):
    bad = tmp_path / "bad.png"; bad.write_bytes(b"not an image")
    with pytest.raises(ConvertError, match="Couldn't read"):
        conv(bad, "jpg")
    assert not list(tmp_path.glob("bad*.jpg"))


# ------------------------------------------------------------------ PDF

@pytest.fixture
def pdf3(tmp_path):
    pages = [Image.new("RGB", (300, 400), c) for c in ("white", "lightblue", "pink")]
    p = tmp_path / "doc.pdf"
    pages[0].save(p, save_all=True, append_images=pages[1:], resolution=72)
    return p


def test_multi_page_pdf_goes_to_a_folder(pdf3):
    out = conv(pdf3, "pdf-png")[0]
    assert out.is_dir() and out.name == "doc pages"
    assert sorted(f.name for f in out.iterdir()) == ["page 1.png", "page 2.png", "page 3.png"]
    w, h = Image.open(out / "page 1.png").size                    # 300x400pt at 150 dpi
    assert abs(w - 625) <= 1 and abs(h - 833) <= 1


def test_single_page_pdf_goes_next_to_the_file(tmp_path):
    p = tmp_path / "one.pdf"; Image.new("RGB", (200, 200), "white").save(p, resolution=72)
    out = conv(p, "pdf-jpg", pdf_dpi=72)[0]
    assert out.is_file() and out.name == "one.jpg" and Image.open(out).size == (200, 200)


def test_pdf_without_text_does_not_create_a_text_file(pdf3):
    with pytest.raises(ConvertError, match="no extractable text"):
        conv(pdf3, "pdf-txt")
    assert not list(pdf3.parent.glob("*.txt"))


def test_pdf_can_be_recycled_after_conversion(pdf3):
    conv(pdf3, "pdf-png"); pdf3.unlink()


def test_broken_pdf(tmp_path):
    bad = tmp_path / "bad.pdf"; bad.write_bytes(b"%PDF-1.4 garbage")
    with pytest.raises(ConvertError):
        conv(bad, "pdf-png")
