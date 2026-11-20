"""Document conversion (DocConv.swift used macOS textutil + AppKit printing).

Windows has no textutil, so a document goes through whichever engine can do the job:

  * LibreOffice  – best fidelity, reads/writes everything incl. old .doc   (optional install)
  * Pandoc       – docx/odt/rtf/html in and out                            (optional install)
  * built-in     – txt, Markdown, HTML, and .docx (via mammoth) → txt / html
  * Edge/Chrome  – prints HTML to PDF (Edge ships with Windows)
"""
from __future__ import annotations

import html.parser
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from . import markdown_render as md
from .formats import Target, ext_of
from .tools import NO_WINDOW, ConvertError, Tool, data_dir, last_line, run_sync, unique_output

INSTALL_LO = "LibreOffice (winget install TheDocumentFoundation.LibreOffice)"
INSTALL_PANDOC = "Pandoc (winget install JohnMacFarlane.Pandoc)"


class Unavailable(Exception):
    """This engine can't do this conversion (or isn't installed); try the next one."""


# --------------------------------------------------------------------- helpers

def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


class _TextExtractor(html.parser.HTMLParser):
    BLOCKS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "table", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.pre = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag == "pre":
            self.pre += 1
            self.out.append("\n")
        elif tag in self.BLOCKS:
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append("\t")
        elif tag == "tr":
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag == "pre":
            self.pre = max(0, self.pre - 1)
            self.out.append("\n")
        elif tag in self.BLOCKS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data if self.pre else re.sub(r"\s+", " ", data))


def html_to_text(src: str) -> str:
    p = _TextExtractor()
    p.feed(src)
    text = "".join(p.out)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ ]+", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def requirement_hint(src_exts: set[str], t: Target) -> Optional[str]:
    """Up-front warning for the UI when a conversion can't work on this machine, else None."""
    if t.id in ("pdf-txt",):
        return None
    out = t.ext
    for e in src_exts:
        try:
            plan_for(e, out)
        except ConvertError as err:
            return str(err)
    return None


# --------------------------------------------------------------------- engines

class Builtin:
    name = "built-in"

    @staticmethod
    def can(inp: str, out: str) -> bool:
        return out in ("txt", "html") and inp in {"txt", "text", "html", "htm", "docx"} | md_exts()

    @staticmethod
    def run(work: Path, inp: str, out: str, tmp: Path, a4: bool) -> Path:
        dst = tmp / f"result.{out}"
        if inp == "docx":
            try:
                import mammoth
            except ImportError:
                raise Unavailable
            with work.open("rb") as f:
                if out == "txt":
                    dst.write_text(mammoth.extract_raw_text(f).value, encoding="utf-8")
                else:
                    body = mammoth.convert_to_html(f).value
                    dst.write_text(md.page(body, work.stem), encoding="utf-8")
            return dst
        text = _read_text(work)
        if inp in md_exts():
            body = md.to_html_body(text)
            if out == "html":
                dst.write_text(md.page(body, work.stem), encoding="utf-8")
            else:
                dst.write_text(html_to_text(body), encoding="utf-8")
        elif inp in ("html", "htm"):
            dst.write_text(text if out == "html" else html_to_text(text), encoding="utf-8")
        else:  # txt
            if out == "txt":
                dst.write_text(text, encoding="utf-8")
            else:
                dst.write_text(md.page(md.text_to_body(text), work.stem), encoding="utf-8")
        return dst


def md_exts() -> set[str]:
    return {"md", "markdown", "mdown", "mkd"}


def to_html_file(work: Path, inp: str, tmp: Path) -> tuple[Path, str]:
    """Markdown and plain text are rendered to HTML first so every engine sees formatted content."""
    if inp in md_exts():
        f = tmp / "rendered.html"
        f.write_text(md.page(md.to_html_body(_read_text(work)), work.stem), encoding="utf-8")
        return f, "html"
    if inp in ("txt", "text"):
        f = tmp / "rendered.html"
        f.write_text(md.page(md.text_to_body(_read_text(work)), work.stem), encoding="utf-8")
        return f, "html"
    return work, inp


class LibreOffice:
    name = "LibreOffice"
    FILTERS = {"pdf": "pdf", "docx": "docx:MS Word 2007 XML", "doc": "doc:MS Word 97", "odt": "odt:writer8",
               "rtf": "rtf:Rich Text Format", "txt": "txt:Text (encoded):UTF8",
               "html": "html:XHTML Writer File:UTF8"}

    @staticmethod
    def can(inp: str, out: str) -> bool:
        return bool(Tool.soffice()) and out in LibreOffice.FILTERS

    @staticmethod
    def run(work: Path, inp: str, out: str, tmp: Path, a4: bool) -> Path:
        exe = Tool.soffice()
        if not exe:
            raise Unavailable
        work, inp = to_html_file(work, inp, tmp)
        outdir = tmp / "lo_out"
        outdir.mkdir(exist_ok=True)
        profile = data_dir() / "lo-profile"          # reused between runs; the first run is slower
        args = [f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore", "--nolockcheck"]
        if inp in ("html", "htm"):                    # open as a Writer document, not Writer/Web
            args.append("--infilter=HTML (StarWriter)")
        args += ["--convert-to", LibreOffice.FILTERS[out], "--outdir", str(outdir), str(work)]
        try:
            r = subprocess.run([exe, *args], capture_output=True, timeout=300, stdin=subprocess.DEVNULL,
                               creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise ConvertError("LibreOffice took too long and was stopped")
        made = next(iter(outdir.glob(f"*.{out}")), None)
        for _ in range(40 if made is None and r.returncode == 0 else 0):   # launcher returned early? wait ~20 s
            time.sleep(0.5)
            made = next(iter(outdir.glob(f"*.{out}")), None)
            if made:
                time.sleep(0.5)
                break
        if made is None:
            raise ConvertError(last_line(r.stderr) or "LibreOffice couldn't convert this file")
        return made


class Pandoc:
    name = "Pandoc"
    READ = {"docx": "docx", "odt": "odt", "rtf": "rtf", "html": "html", "htm": "html", "txt": "html", "text": "html"}
    WRITE = {"docx": "docx", "odt": "odt", "rtf": "rtf", "html": "html", "txt": "plain"}

    @staticmethod
    def can(inp: str, out: str) -> bool:
        return bool(Tool.pandoc()) and (inp in Pandoc.READ or inp in md_exts()) and out in Pandoc.WRITE

    @staticmethod
    def run(work: Path, inp: str, out: str, tmp: Path, a4: bool) -> Path:
        exe = Tool.pandoc()
        if not exe:
            raise Unavailable
        src, inp = to_html_file(work, inp, tmp)
        frm = Pandoc.READ[inp]
        dst = tmp / f"result.{out}"
        args = ["-f", frm, "-t", Pandoc.WRITE[out], "-o", str(dst), str(src)]
        if out == "html":
            args[4:4] = ["--standalone", "--metadata", f"title={work.stem}"]
        if out == "rtf":
            args[4:4] = ["--standalone"]             # without it the RTF has no {\rtf1 header and Word rejects it
        if out == "txt":
            args[4:4] = ["--wrap=none"]
        run_sync(exe, args, timeout=180)
        return dst


class Browser:
    """Prints HTML to PDF through Edge (preinstalled on Windows) or Chrome, headless."""
    name = "Edge"

    @staticmethod
    def can(inp: str, out: str) -> bool:
        return bool(Tool.browser()) and out == "pdf" and (inp in {"html", "htm", "txt", "text", "docx"} | md_exts())

    @staticmethod
    def prepare_html(work: Path, inp: str, tmp: Path, a4: bool) -> Path:
        paper = "A4" if a4 else "Letter"
        page_css = f"@page {{ size: {paper}; margin: 20mm; }}"
        dst = tmp / "print.html"
        if inp in md_exts():
            dst.write_text(md.page(md.to_html_body(_read_text(work)), work.stem, paper), encoding="utf-8")
        elif inp in ("txt", "text"):
            dst.write_text(md.page(md.text_to_body(_read_text(work)), work.stem, paper), encoding="utf-8")
        elif inp == "docx":
            try:
                import mammoth
            except ImportError:
                raise Unavailable
            with work.open("rb") as f:
                dst.write_text(md.page(mammoth.convert_to_html(f).value, work.stem, paper), encoding="utf-8")
        else:                                         # user's own HTML: keep it, add paper size + base for its images
            src = _read_text(work)
            inject = f'<base href="{work.parent.resolve().as_uri()}/"><style>{page_css}</style>'
            m = re.search(r"<head[^>]*>", src, re.I)
            src = src[:m.end()] + inject + src[m.end():] if m else inject + src
            dst.write_text(src, encoding="utf-8")
        return dst

    @staticmethod
    def run(work: Path, inp: str, out: str, tmp: Path, a4: bool) -> Path:
        exe = Tool.browser()
        if not exe:
            raise Unavailable
        page = Browser.prepare_html(work, inp, tmp, a4)
        dst = tmp / "result.pdf"
        profile = tmp / "browser-profile"           # own profile, else Edge hands off to a running Edge and exits
        args = ["--headless", "--disable-gpu", "--no-first-run", "--no-pdf-header-footer",
                f"--user-data-dir={profile}",
                "--host-resolver-rules=MAP * ~NOTFOUND",     # nothing leaves the machine, as promised
                f"--print-to-pdf={dst}", page.resolve().as_uri()]
        try:
            r = subprocess.run([exe, *args], capture_output=True, timeout=120, stdin=subprocess.DEVNULL,
                               creationflags=NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise Unavailable
        if not dst.exists() or dst.stat().st_size == 0:
            raise Unavailable
        return dst


class PandocThenBrowser:
    """odt/rtf → HTML with Pandoc → PDF with Edge, for when LibreOffice isn't installed."""
    name = "Pandoc + Edge"

    @staticmethod
    def can(inp: str, out: str) -> bool:
        return out == "pdf" and inp in ("odt", "rtf") and bool(Tool.pandoc()) and bool(Tool.browser())

    @staticmethod
    def run(work: Path, inp: str, out: str, tmp: Path, a4: bool) -> Path:
        html_out = Pandoc.run(work, inp, "html", tmp, a4)
        return Browser.run(html_out, "html", "pdf", tmp, a4)


def plan_for(inp: str, out: str) -> list:
    """Engines to try, best first. Raises ConvertError with install advice if none can do it."""
    office_like = inp in ("doc", "docx", "odt", "rtf")
    if out == "pdf":
        order = ([LibreOffice, Browser, PandocThenBrowser] if office_like
                 else [Browser, LibreOffice])
    elif out in ("txt", "html"):
        order = [Builtin, Pandoc, LibreOffice]
    else:
        order = [LibreOffice, Pandoc]
    engines = [e for e in order if e.can(inp, out)]
    if engines:
        return engines
    needs = []
    if out == "pdf" and inp in {"html", "htm", "txt", "text", "docx"} | md_exts():
        needs += ["Microsoft Edge or Google Chrome", INSTALL_LO]
    elif inp == "doc" or out == "doc":
        needs.append(INSTALL_LO)
    else:
        needs += [INSTALL_LO, INSTALL_PANDOC] if out != "pdf" else [INSTALL_LO]
    raise ConvertError(f"Converting .{inp} to {out.upper()} needs " + " or ".join(needs))


# --------------------------------------------------------------------- public

def convert(path: Path, t: Target, a4: bool) -> Path:
    inp = ext_of(path)
    out = t.ext
    engines = plan_for(inp, out)
    with tempfile.TemporaryDirectory(prefix="convert_") as td:
        tmp = Path(td)
        last_err: Exception | None = None
        for eng in engines:
            try:
                made = eng.run(path, inp, out, tmp, a4)
            except Unavailable:
                continue
            except ConvertError as e:
                last_err = e
                continue
            dest = unique_output(path, out)
            shutil.copyfile(made, dest)
            return dest
        if last_err:
            raise last_err
        raise ConvertError("Couldn't convert this document")
