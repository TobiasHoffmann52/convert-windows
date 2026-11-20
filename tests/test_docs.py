import os
import shutil
import pytest

from convert_win import converter, docs, tools
from convert_win.formats import ConvSettings, Targets
from convert_win.tools import ConvertError, Runner
from conftest import need

MD = "# Title\n\nSome **bold** and *it* text.\n\n- [x] done\n- [ ] open\n\n| A | B |\n|---|---|\n| 1 | 2 |\n"


def conv(path, tid, **s):
    return converter.convert(path, Targets.by_id(tid), ConvSettings(**s), Runner())[0]


@pytest.fixture
def engines(monkeypatch):
    """Only the built-in engine, unless a test turns more on."""
    for v in ("CONVERT_SOFFICE", "CONVERT_PANDOC", "CONVERT_BROWSER"):
        monkeypatch.setenv(v, "none")
    tools.clear_cache()
    yield monkeypatch
    tools.clear_cache()


@pytest.fixture
def md(tmp_path):
    p = tmp_path / "notes.md"; p.write_text(MD, "utf-8"); return p


def test_markdown_to_html_renders_everything(engines, md):
    h = conv(md, "html").read_text("utf-8")
    assert "<h1>Title</h1>" in h and "<strong>bold</strong>" in h and "<table>" in h
    assert "☑" in h and "☐" in h and "<input" not in h


def test_markdown_images_become_alt_text_and_raw_html_is_not_rendered(engines, tmp_path):
    p = tmp_path / "x.md"; p.write_text("![the alt](http://evil.example/a.png)\n\n<script>alert(1)</script>\n")
    h = conv(p, "html").read_text("utf-8")
    assert "the alt" in h and "<img" not in h and "<script>" not in h


def test_markdown_to_text_is_plain(engines, md):
    t = conv(md, "txt").read_text("utf-8")
    assert "Title" in t and "bold" in t and "**" not in t and "#" not in t and "☑ done" in t


def test_text_file_keeps_unicode_and_lines(engines, tmp_path):
    p = tmp_path / "t.txt"; p.write_text("Zeile eins äöü €\nZeile zwei\n\nNeu\n", "utf-8")
    h = conv(p, "html").read_text("utf-8")
    assert "äöü €" in h and "<br>" in h


def test_legacy_encoding_text_file(engines, tmp_path):
    p = tmp_path / "old.txt"; p.write_bytes("café — naïve".encode("cp1252"))
    assert "café" in conv(p, "html").read_text("utf-8")


def test_docx_to_text_via_mammoth(engines, tmp_path):
    import zipfile
    # minimal docx
    p = tmp_path / "m.docx"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Hello Windows</w:t></w:r></w:p></w:body></w:document>')
    assert "Hello Windows" in conv(p, "txt").read_text("utf-8")


def test_missing_engines_give_actionable_errors(engines, md, tmp_path):
    with pytest.raises(ConvertError, match="Pandoc"):
        conv(md, "docx")
    with pytest.raises(ConvertError, match="LibreOffice"):
        conv(tmp_path / "a.doc", "txt") if False else docs.plan_for("doc", "txt")
    with pytest.raises(ConvertError, match="Edge"):
        conv(md, "pdf-doc")


def test_hint_for_ui_warns_before_converting(engines):
    t = Targets.by_id("docx")
    assert "Pandoc" in docs.requirement_hint({"md"}, t)
    assert docs.requirement_hint({"md"}, Targets.by_id("html")) is None


@need("pandoc")
def test_pandoc_routes(engines, md, tmp_path):
    engines.setenv("CONVERT_PANDOC", shutil.which("pandoc")); tools.clear_cache()
    docx = conv(md, "docx")
    assert docx.exists()
    assert "Title" in conv(docx, "txt").read_text("utf-8")      # docx → txt (built-in/mammoth)
    odt = conv(docx, "odt"); rtf = conv(docx, "rtf")
    assert odt.suffix == ".odt" and rtf.read_bytes().startswith(b"{\\rtf")
    assert "Title" in conv(odt, "txt").read_text("utf-8")


# ---- Edge/Chrome route, with a stand-in browser that records how it was called
@pytest.fixture
def fake_browser(tmp_path, engines):
    import json, sys
    log = tmp_path / "log.json"
    script = tmp_path / "fakebrowser.py"
    script.write_text(f'''import sys, json, urllib.parse, pathlib
a = sys.argv[1:]
out = [x.split("=", 1)[1] for x in a if x.startswith("--print-to-pdf=")][0]
html = pathlib.Path(urllib.parse.unquote(a[-1].replace("file:///", "/").lstrip("/") if False else a[-1][len("file://"):].lstrip("/") if False else a[-1])).exists()
page = urllib.parse.unquote(urllib.parse.urlparse(a[-1]).path)
if page[:1] == "/" and page[2:3] == ":": page = page[1:]
src = pathlib.Path(page).read_text("utf-8")
json.dump({{"args": a[:-1], "a4": "size: A4" in src, "letter": "size: Letter" in src, "base": "<base href" in src,
          "h1": "<h1>" in src}}, open({str(log)!r}, "w"))
open(out, "wb").write(b"%PDF-1.4 fake")
''')
    if sys.platform == "win32":
        pytest.skip("stand-in uses a POSIX shebang")
    script.write_text("#!" + sys.executable + "\n" + script.read_text())
    script.chmod(0o755)
    engines.setenv("CONVERT_BROWSER", str(script)); tools.clear_cache()
    return log


def test_browser_prints_markdown_with_isolated_profile_and_no_network(fake_browser, md):
    out = conv(md, "pdf-doc")
    assert out.read_bytes().startswith(b"%PDF")
    import json
    log = json.loads(fake_browser.read_text())
    assert log["a4"] and log["h1"]
    flags = " ".join(log["args"])
    assert "--headless" in flags and "--user-data-dir=" in flags and "--host-resolver-rules=MAP * ~NOTFOUND" in flags
    assert "--no-pdf-header-footer" in flags


def test_paper_size_and_html_base(fake_browser, tmp_path):
    import json
    h = tmp_path / "page.html"; h.write_text("<html><head><title>x</title></head><body><img src='a.png'>hi</body></html>")
    conv(h, "pdf-doc", paper_a4=False)
    log = json.loads(fake_browser.read_text())
    assert log["letter"] and not log["a4"] and log["base"]
