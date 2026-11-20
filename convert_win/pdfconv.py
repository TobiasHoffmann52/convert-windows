"""PDF → images / text with pdfium (PDFConv in DocConv.swift used Core Graphics/PDFKit)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from .formats import ConvSettings, Target
from .tools import ConvertError, unique_folder, unique_output


def _open(path: Path):
    import pypdfium2 as pdfium
    try:
        return pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as e:
        if "password" in str(e).lower():
            raise ConvertError("This PDF is password-protected")
        raise ConvertError("Couldn't open this PDF")


def convert(path: Path, t: Target, s: ConvSettings, progress: Callable[[float], None] = lambda p: None) -> list[Path]:
    doc = _open(path)
    try:
        count = len(doc)
        if count == 0:
            raise ConvertError("This PDF has no pages")
        if t.id == "pdf-txt":
            parts = []
            for i in range(count):
                page = doc[i]
                tp = page.get_textpage()
                parts.append(tp.get_text_range().replace("\r\n", "\n"))
                tp.close(); page.close()
            text = "\n\n".join(parts)
            if not text.strip():
                raise ConvertError("This PDF contains no extractable text")
            out = unique_output(path, "txt")
            out.write_text(text, encoding="utf-8")
            return [out]

        folder = None
        if count > 1:                      # several pages → a folder with page 1, page 2, …
            folder = unique_folder(path, " pages")
            folder.mkdir(parents=True)
        single = unique_output(path, t.ext)
        fmt = "JPEG" if t.id == "pdf-jpg" else "PNG"
        kw = {"quality": round(s.image_quality * 100), "optimize": True} if fmt == "JPEG" else {}
        scale = s.pdf_dpi / 72
        for i in range(count):
            page = doc[i]
            try:
                img = page.render(scale=scale).to_pil().convert("RGB")
            except Exception:
                raise ConvertError(f"Couldn't render page {i + 1}")
            finally:
                page.close()
            out = folder / f"page {i + 1}.{t.ext}" if folder else single
            img.save(out, format=fmt, **kw)
            progress((i + 1) / count)
        return [folder or single]
    finally:
        doc.close()          # Windows can't recycle a file that's still open
