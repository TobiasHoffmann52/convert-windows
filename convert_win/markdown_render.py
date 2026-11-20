"""Markdown → styled standalone HTML (Markdown.swift)."""
from __future__ import annotations

import html as _html

CSS = """
body { font-family: "Segoe UI", Arial, Helvetica, sans-serif; font-size: 11pt; line-height: 1.5; color: #1d1d1f; }
h1 { font-size: 22pt; margin: 0 0 10pt; } h2 { font-size: 16pt; margin: 18pt 0 8pt; }
h3 { font-size: 13pt; margin: 14pt 0 6pt; } h4, h5, h6 { font-size: 11pt; margin: 12pt 0 4pt; }
p, ul, ol, blockquote, pre, table { margin: 0 0 9pt; }
code { font-family: Consolas, "Courier New", monospace; font-size: 9.5pt; background: #f2f2f4; }
pre { font-family: Consolas, "Courier New", monospace; font-size: 9.5pt; background: #f2f2f4; padding: 8pt; white-space: pre-wrap; }
pre code { background: none; }
blockquote { color: #555; margin-left: 0; padding: 4pt 10pt; border-left: 3pt solid #d0d0d6; background: #f7f7f9; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #d0d0d6; padding: 4pt 8pt; text-align: left; }
th { background: #f2f2f4; } a { color: #0a66d8; }
hr { border: 0; border-top: 1px solid #d0d0d6; margin: 14pt 0; }
ul.task { list-style: none; padding-left: 4pt; }
"""


def escape(s: str) -> str:
    return _html.escape(s, quote=True)


def to_html_body(source: str) -> str:
    from markdown_it import MarkdownIt
    from mdit_py_plugins.tasklists import tasklists_plugin
    md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable(["table", "strikethrough"])
    md.use(tasklists_plugin)

    def image(self, tokens, idx, options, env):      # images aren't rendered; show the alt text (as on macOS)
        return escape(tokens[idx].content)
    md.add_render_rule("image", image)
    body = md.render(source)
    # The tasklist plugin emits <input type=checkbox>; use print-friendly glyphs instead.
    import re
    body = re.sub(r'<input[^>]*task-list-item-checkbox[^>]*checked[^>]*>', "☑", body)
    body = re.sub(r'<input[^>]*task-list-item-checkbox[^>]*>', "☐", body)
    return body


def page(body: str, title: str, paper: str | None = None) -> str:
    size = f"@page {{ size: {paper}; margin: 20mm; }}" if paper else ""
    return (f'<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>{escape(title)}</title>\n'
            f"<style>{CSS}{size}</style></head><body>\n{body}\n</body></html>\n")


def text_to_body(text: str) -> str:
    """Plain text: keep line breaks, blank lines separate paragraphs."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    paras = [p for p in text.split("\n\n")]
    return "\n".join("<p>" + escape(p).replace("\n", "<br>\n") + "</p>" for p in paras if p.strip())


def is_markdown(path) -> bool:
    return str(path).lower().rsplit(".", 1)[-1] in {"md", "markdown", "mdown", "mkd"}
