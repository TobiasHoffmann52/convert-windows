"""The conversion window (ContentView.swift + AppDelegate.swift, rebuilt with tkinter + Sun Valley theme)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk

from . import __version__, caps, shell
from .formats import ALL_INPUT_EXTS, MediaKind, OutGroup
from .icons import kind_icon
from .model import DONE, IDLE, RUNNING, ConvertModel
from .tools import IS_WINDOWS

BRAND = "#2EA8FF"
LABEL_W = 104
SCALE = 1.0        # set by the app from Tk's DPI scaling


def px(n: float) -> int:
    return max(1, round(n * SCALE))


# Base text sizes in pixels at 100% Windows scale. The Sun Valley theme sizes its fonts in pixels (which Tk does not
# scale for high-DPI screens) while Tk's own fonts are in points (which it does), so left alone the two disagree.
_BASE_PX = {
    "SunValleyBodyFont": 14, "SunValleyBodyLargeFont": 18, "SunValleyBodyStrongFont": 14,
    "SunValleyCaptionFont": 12, "SunValleySubtitleFont": 20, "SunValleyTitleFont": 28,
    "SunValleyTitleLargeFont": 40, "SunValleyDisplayFont": 68,
    "TkDefaultFont": 14, "TkTextFont": 14, "TkMenuFont": 14, "TkHeadingFont": 14, "TkIconFont": 14,
    "TkCaptionFont": 14, "TkSmallCaptionFont": 12, "TkTooltipFont": 12, "TkFixedFont": 13,
    "ConvertTitleFont": 20, "ConvertGlyphFont": 30,
}
TEXT_SIZES = (1.0, 1.15, 1.3, 1.5, 1.75, 2.0)
_font_refs: dict = {}      # Tk deletes a named font when its Python object is garbage-collected, so keep them alive


def apply_fonts(root, text_scale: float) -> None:
    """Size every font from one rule: base pixels × Windows scale × the user's text-size choice."""
    import tkinter.font as tkfont
    for name, base in _BASE_PX.items():
        f = _font_refs.get(name)
        if f is None:
            try:
                f = tkfont.nametofont(name, root=root)
            except tk.TclError:
                f = tkfont.Font(root=root, name=name, exists=False,
                                family=tkfont.nametofont("TkDefaultFont", root=root).actual("family"),
                                weight="bold" if name == "ConvertTitleFont" else "normal")
            _font_refs[name] = f
        f.configure(size=-max(8, round(base * SCALE * text_scale)))


def rate_label(hz: int) -> str:
    k = hz / 1000
    return f"{int(k)} kHz" if k == int(k) else f"{k:.1f} kHz"


class Panel:
    """One window with one batch of files."""

    def __init__(self, app, files: list[Path]) -> None:
        self.app = app
        self.root = app.root
        self.created = time.time()
        self.model = ConvertModel(files, store=app.store)
        self.win = tk.Toplevel(self.root)
        self.win.title("Convert")
        self.win.resizable(False, False)
        self._set_icon()
        self.win.protocol("WM_DELETE_WINDOW", self.close)
        self.show_advanced = tk.BooleanVar(value=bool(app.store.get("showAdvanced", False)))
        self._images: list = []
        self._sig = None
        self.installing = False
        self._build_menu()
        self.outer = ttk.Frame(self.win, padding=px(20))
        self.outer.pack(fill="both", expand=True)
        tk.Frame(self.outer, width=px(420), height=1).pack()      # fixed content width, like the Mac panel
        self.header = ttk.Frame(self.outer)
        self.body = ttk.Frame(self.outer)
        self.footer = ttk.Frame(self.outer)
        self.header.pack(fill="x")
        self.body.pack(fill="x", pady=(px(16), 0))
        ttk.Separator(self.outer).pack(fill="x", pady=px(16))
        self.footer.pack(fill="x")
        self.win.bind("<Escape>", lambda e: self._escape())
        self.win.bind("<Return>", lambda e: self._enter())
        self._enable_drop(self.win)
        self.refresh()
        self._center()

    # ------------------------------------------------------------ window plumbing
    def _set_icon(self) -> None:
        try:
            ico = Path(__file__).parent / "assets" / "icon.ico"
            png = Path(__file__).parent / "assets" / "icon.png"
            if IS_WINDOWS and ico.exists():
                self.win.iconbitmap(default=str(ico))
            elif png.exists():
                self._icon = tk.PhotoImage(file=str(png))
                self.win.iconphoto(True, self._icon)
        except tk.TclError:
            pass

    def _center(self) -> None:
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        n = len(self.app.panels)
        x = (self.win.winfo_screenwidth() - w) // 2 + 28 * n
        y = (self.win.winfo_screenheight() - h) // 3 + 28 * n
        self.win.geometry(f"+{x}+{y}")
        self.win.lift(); self.win.focus_force()

    def _enable_drop(self, widget) -> None:
        if not self.app.dnd:
            return
        try:
            widget.drop_target_register(self.app.dnd_files)
            widget.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    def _on_drop(self, event) -> None:
        paths = [Path(p) for p in self.root.tk.splitlist(event.data)]
        self.model.add(paths)
        self.refresh()

    def _build_menu(self) -> None:
        bar = tk.Menu(self.win)
        f = tk.Menu(bar, tearoff=0)
        f.add_command(label="Open…", accelerator="Ctrl+O", command=self.choose_files)
        f.add_command(label="Close", accelerator="Ctrl+W", command=self.close)
        bar.add_cascade(label="File", menu=f)
        c = tk.Menu(bar, tearoff=0)
        c.add_command(label="Add Convert… to the right-click menu", command=self.install_menu)
        c.add_command(label="Remove Convert… from the right-click menu", command=self.remove_menu)
        c.add_separator()
        size = tk.Menu(c, tearoff=0)
        self.text_var = tk.DoubleVar(value=self.app.text_scale)
        for v in TEXT_SIZES:
            size.add_radiobutton(label=f"{round(v * 100)}%", variable=self.text_var, value=v,
                                 command=lambda: self.app.set_text_scale(self.text_var.get()))
        c.add_cascade(label="Text size", menu=size)
        c.add_separator()
        c.add_command(label="Tool check…", command=self.show_tools)
        c.add_command(label="About Convert", command=self.about)
        bar.add_cascade(label="Convert", menu=c)
        self.win.config(menu=bar)
        self.win.bind("<Control-o>", lambda e: self.choose_files())
        self.win.bind("<Control-w>", lambda e: self.close())
        for key, step in (("<Control-plus>", 1), ("<Control-equal>", 1), ("<Control-KP_Add>", 1),
                          ("<Control-minus>", -1), ("<Control-KP_Subtract>", -1)):
            self.win.bind(key, lambda e, st=step: self.app.step_text_scale(st))
        self.win.bind("<Control-0>", lambda e: self.app.set_text_scale(1.0))

    def close(self) -> None:
        self.model.cancel()
        self.app.panel_closed(self)
        self.win.destroy()

    def _escape(self) -> None:
        if self.model.phase == RUNNING:
            self.model.cancel()
        else:
            self.close()

    def _enter(self) -> None:
        if self.model.phase == IDLE and self.model.target and self.model.files:
            self._start()
        elif self.model.phase == DONE:
            self.close()

    # ------------------------------------------------------------ dialogs / actions
    def choose_files(self) -> None:
        types = [("Supported files", " ".join(f"*.{e}" for e in ALL_INPUT_EXTS)), ("All files", "*.*")]
        names = filedialog.askopenfilenames(parent=self.win, title="Choose files", filetypes=types)
        if names:
            self.model.add([Path(n) for n in self.root.tk.splitlist(names)])
            self.refresh()

    def install_menu(self) -> None:
        try:
            shell.install()
            messagebox.showinfo(
                "Right-click menu added",
                "Right-click a file in File Explorer and choose Convert…\n\n"
                "On Windows 11 it's under “Show more options” (or press Shift+F10 on the selected files).",
                parent=self.win)
        except Exception as e:
            messagebox.showerror("Couldn't add the right-click menu", str(e), parent=self.win)
        self.app.refresh_all()

    def remove_menu(self) -> None:
        try:
            shell.uninstall()
        except Exception as e:
            messagebox.showerror("Couldn't remove the right-click menu", str(e), parent=self.win)
        self.app.refresh_all()

    def install_ffmpeg(self) -> None:
        """One-click ffmpeg via winget, which ships with current Windows 10/11."""
        from . import tools
        manual = "Open PowerShell and run:\n\nwinget install Gyan.FFmpeg"
        winget = tools.Tool.winget()
        if not winget:
            messagebox.showinfo("Install ffmpeg", "winget wasn't found on this PC.\n\n" + manual, parent=self.win)
            return
        self.installing = True
        self._install_result: list = []
        self.refresh()

        def work() -> None:
            try:
                r = subprocess.run([winget, "install", "--id", "Gyan.FFmpeg", "-e", "--silent",
                                    "--accept-source-agreements", "--accept-package-agreements"],
                                   capture_output=True, stdin=subprocess.DEVNULL, timeout=900,
                                   creationflags=tools.NO_WINDOW)
                self._install_result.append(r.returncode)
            except Exception as e:
                self._install_result.append(e)
        import threading
        threading.Thread(target=work, daemon=True).start()
        self._watch_install(manual)

    def _watch_install(self, manual: str) -> None:
        if not self.win.winfo_exists():
            return
        if not self._install_result:
            self.win.after(500, lambda: self._watch_install(manual))
            return
        from . import tools
        self.installing = False
        tools.clear_cache()                       # Explorer's PATH is stale; the finder also checks winget's folders
        caps.encoders.cache_clear()
        caps._hw_cache.clear()
        self.app.caps_ready.clear()
        import threading
        threading.Thread(target=self.app._warm_caps, daemon=True).start()
        if tools.Tool.ffmpeg():
            messagebox.showinfo("ffmpeg installed", "Audio and video conversion is ready.", parent=self.win)
        else:
            messagebox.showwarning("Couldn't install ffmpeg", "The automatic install didn't finish.\n\n" + manual,
                                   parent=self.win)
        self._sig = None
        self.refresh()

    def show_tools(self) -> None:
        from .tools import Tool
        rows = [("ffmpeg (audio, video)", Tool.ffmpeg()), ("LibreOffice (documents)", Tool.soffice()),
                ("Pandoc (documents)", Tool.pandoc()), ("Edge / Chrome (PDF output)", Tool.browser())]
        text = "\n".join(f"{n}: {p or 'not found'}" for n, p in rows)
        text += f"\n\nGPU video encoding: {caps.hw_encoder('h264') or 'not available'}"
        messagebox.showinfo("Tool check", text, parent=self.win)

    def about(self) -> None:
        messagebox.showinfo("About Convert", f"Convert for Windows {__version__}\n\nConvert files locally. "
                            "Nothing is uploaded.\n\nWindows port by Tobias Hoffmann.\n"
                            "Based on Convert for macOS by eliasdesena (MIT).", parent=self.win)

    def _start(self) -> None:
        self.model.start()
        self.refresh()
        self._tick()

    def _show_in_explorer(self) -> None:
        outs = self.model.outputs
        if not outs:
            return
        try:
            if IS_WINDOWS:
                subprocess.Popen(["explorer", "/select,", str(outs[0])])
            else:
                os.startfile(str(outs[0].parent))      # type: ignore[attr-defined]
        except Exception:
            pass

    # ------------------------------------------------------------ rendering
    def _signature(self):
        m = self.model
        return (m.phase, tuple(m.files), m.target_id, m.skipped, len(m.errors), m.stopped, m.trashed,
                self.show_advanced.get(), self.app.caps_ready.is_set(), shell.is_installed())

    def refresh(self) -> None:
        sig = self._signature()
        if sig == self._sig:
            return
        self._sig = sig
        for f in (self.header, self.body, self.footer):
            for w in f.winfo_children():
                w.destroy()
        self._images = []
        self._draw_header()
        self._draw_body()
        self._draw_footer()
        self.win.update_idletasks()

    def _icon_image(self, kind, size):
        img = ImageTk.PhotoImage(kind_icon(kind, px(size), self.app.dark))
        self._images.append(img)
        return img

    def _draw_header(self) -> None:
        m = self.model
        k = m.primary_kind
        icon = ttk.Label(self.header, image=self._icon_image(k.value if k else "app", 44))
        icon.pack(side="left")
        text = ttk.Frame(self.header)
        text.pack(side="left", padx=px(12), fill="x", expand=True)
        title = {0: "Convert", 1: m.files[0].name if m.files else ""}.get(len(m.files), f"{len(m.files)} files")
        ttk.Label(text, text=title, font="ConvertTitleFont", wraplength=px(380)).pack(anchor="w")
        ttk.Label(text, text=self._subtitle(), foreground="gray", wraplength=px(380),
                  justify="left").pack(anchor="w")

    def _subtitle(self) -> str:
        m = self.model
        if not m.files:
            return "Drop files here, or right-click files in File Explorer → Convert…"
        parts = []
        if len(m.files) == 1:
            f = m.files[0]
            k = MediaKind.of(f)
            if k:
                parts.append(k.noun)
            try:
                parts.append(self._size(f.stat().st_size))
            except OSError:
                pass
        else:
            names = ", ".join(f.name for f in m.files[:3])
            parts.append(f"{names} + {len(m.files) - 3} more" if len(m.files) > 3 else names)
        if m.skipped:
            parts.append(f"{m.skipped} unsupported skipped")
        return " · ".join(parts)

    @staticmethod
    def _size(n: float) -> str:
        for unit in ("bytes", "KB", "MB", "GB"):
            if n < 1000 or unit == "GB":
                return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
            n /= 1000
        return ""

    # ---- body
    def _draw_body(self) -> None:
        m = self.model
        b = self.body
        if not m.files:
            self._drop_zone(b)
        elif not m.groups:
            ttk.Label(b, text="⚠  These files can't be converted together. Pick files of one kind "
                              "(audio, video, images, documents or PDFs).", wraplength=px(420),
                      foreground="gray").pack(anchor="w")
        elif m.target:
            row = self._row(b, "Convert to")
            self._format_picker(row)
            if m.target.has_settings:
                toggle = self._row(b, "")
                arrow = "▾" if self.show_advanced.get() else "▸"
                link = ttk.Button(toggle, text=f"{arrow} Advanced", style="Toolbutton",
                                  command=self._toggle_advanced)
                link.pack(anchor="w")
                if self.show_advanced.get():
                    state = "disabled" if m.phase != IDLE else "normal"
                    SettingsView(self, b, m, state).build()

    def _toggle_advanced(self) -> None:
        self.show_advanced.set(not self.show_advanced.get())
        self.app.store.set("showAdvanced", self.show_advanced.get())
        self.refresh()
        self.win.geometry("")

    def _row(self, parent, label: str):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=px(4))
        ttk.Label(row, text=label, foreground="gray", width=14, anchor="e").pack(side="left", padx=(0, px(12)))
        inner = ttk.Frame(row)
        inner.pack(side="left", fill="x", expand=True)
        return inner

    def _drop_zone(self, parent) -> None:
        canvas = tk.Canvas(parent, height=px(130), highlightthickness=0, bd=0,
                           bg=self.app.bg)
        canvas.pack(fill="x")
        inner = ttk.Frame(canvas)
        canvas.create_window(0, 0, window=inner, anchor="center", tags="inner")
        canvas.bind("<Configure>", lambda e: (canvas.delete("frame"), canvas.create_rectangle(
            2, 2, e.width - 2, e.height - 2, dash=(6, 5), outline="#8a8a8a", width=1.5, tags="frame"),
            canvas.coords("inner", e.width / 2, e.height / 2)))
        ttk.Label(inner, text="⭳", font="ConvertGlyphFont", foreground="gray").pack()
        ttk.Label(inner, text="Drop audio, video, images, documents or PDFs", foreground="gray").pack(pady=px(4))
        ttk.Button(inner, text="Choose Files…", command=self.choose_files).pack()
        self._enable_drop(canvas)
        if not shell.is_installed() and IS_WINDOWS:
            bar = ttk.Frame(parent, padding=px(10))
            bar.pack(fill="x", pady=(px(12), 0))
            ttk.Label(bar, text="Add “Convert…” to File Explorer’s right-click menu").pack(side="left")
            ttk.Button(bar, text="Install", command=self.install_menu).pack(side="right")

    def _format_picker(self, parent) -> None:
        m = self.model
        label = m.target.label if m.target else ""
        btn = ttk.Menubutton(parent, text=label, direction="below")
        menu = tk.Menu(btn, tearoff=0)
        var = tk.StringVar(value=m.target_id)
        multi = len(m.groups) > 1
        for gi, (g, targets) in enumerate(m.groups):
            if multi:
                if gi:
                    menu.add_separator()
                menu.add_command(label=g.value, state="disabled")
            for t in targets:
                menu.add_radiobutton(label=t.label, variable=var, value=t.id,
                                     command=lambda tid=t.id: self._pick(tid))
        btn["menu"] = menu
        btn.configure(state="normal" if m.phase == IDLE else "disabled")
        btn.pack(anchor="w")

    def _pick(self, tid: str) -> None:
        self.model.set_target(tid)
        self.refresh()
        self.win.geometry("")

    # ---- footer
    def _draw_footer(self) -> None:
        m = self.model
        f = self.footer
        if m.phase == IDLE:
            left = ttk.Frame(f)
            left.pack(side="left")
            hint = m.hint()
            if self.installing:
                ttk.Label(left, text="Installing ffmpeg… this can take a minute", foreground="gray",
                          wraplength=px(250)).pack(anchor="w")
            elif hint and hint.startswith("Needs ffmpeg"):
                ttk.Label(left, text="⚠ Audio and video need ffmpeg", foreground="#d98200",
                          wraplength=px(250)).pack(anchor="w")
                ttk.Button(left, text="Install ffmpeg", command=self.install_ffmpeg).pack(anchor="w", pady=(px(4), 0))
            elif hint:
                ttk.Label(left, text="⚠ " + hint, foreground="#d98200", wraplength=px(250)).pack(anchor="w")
            else:
                var = tk.BooleanVar(value=m.trash_originals)
                text = "Move originals to Recycle Bin" if len(m.files) > 1 else "Move original to Recycle Bin"
                ttk.Checkbutton(left, text=text, variable=var,
                                command=lambda: m.set_trash_originals(var.get())).pack(anchor="w")
            ttk.Button(f, text="Convert", style="Accent.TButton", command=self._start,
                       state="normal" if (m.target and m.files) else "disabled").pack(side="right")
            ttk.Button(f, text="Cancel", command=self.close).pack(side="right", padx=px(8))
        elif m.phase == RUNNING:
            self.bar = ttk.Progressbar(f, maximum=1000, value=m.progress * 1000)
            self.bar.pack(fill="x")
            row = ttk.Frame(f)
            row.pack(fill="x", pady=(px(8), 0))
            self.status = ttk.Label(row, text=f"Converting {m.current_name}…", foreground="gray")
            self.status.pack(side="left")
            ttk.Button(row, text="Stop", command=m.cancel).pack(side="right")
        else:
            if m.outputs:
                n = len(m.outputs)
                ttk.Label(f, text=f"✔  Converted {n} {'file' if n == 1 else 'files'}", foreground="#1a9b3c").pack(anchor="w")
            if m.trashed:
                what = "the original" if m.trashed == 1 else f"{m.trashed} originals"
                ttk.Label(f, text=f"Moved {what} to the Recycle Bin", foreground="gray").pack(anchor="w")
            if m.stopped:
                ttk.Label(f, text="Stopped", foreground="gray").pack(anchor="w")
            for e in m.errors:
                ttk.Label(f, text="✖  " + e, foreground="#d13438", wraplength=px(420), justify="left").pack(anchor="w")
            row = ttk.Frame(f)
            row.pack(fill="x", pady=(px(8), 0))
            ttk.Button(row, text="Done", style="Accent.TButton", command=self.close).pack(side="right")
            ttk.Button(row, text="Show in Explorer", command=self._show_in_explorer,
                       state="normal" if m.outputs else "disabled").pack(side="right", padx=px(8))

    def _tick(self) -> None:
        """Poll the worker thread; Tk isn't safe to touch from other threads."""
        if not self.win.winfo_exists():
            return
        m = self.model
        if m.phase == RUNNING:
            try:
                self.bar["value"] = m.progress * 1000
                self.status["text"] = f"Converting {m.current_name}…"
            except (tk.TclError, AttributeError):
                pass
            self.win.after(100, self._tick)
        else:
            self.refresh()


class SettingsView:
    """Per-format options under 'Advanced' (SettingsView in ContentView.swift)."""

    def __init__(self, panel: Panel, parent, model: ConvertModel, state: str) -> None:
        self.p, self.parent, self.m, self.state = panel, parent, model, state
        self.t = model.target

    def build(self) -> None:
        g = self.t.group
        if g == OutGroup.audio: self.audio()
        elif g == OutGroup.video: self.gif() if self.t.id == "gif" else self.video()
        elif g == OutGroup.image: self.image()
        elif g == OutGroup.document: self.document()
        else: self.pdf()

    # -- widgets
    def menu(self, label: str, field: str, options: list[tuple]) -> None:
        row = self.p._row(self.parent, label)
        names = [n for _, n in options]
        current = getattr(self.m.settings, field)
        var = tk.StringVar(value=next((n for v, n in options if v == current), names[0]))
        box = ttk.Combobox(row, values=names, textvariable=var, state="readonly", width=24)
        box.pack(anchor="w")
        if self.state == "disabled":
            box.state(["disabled"])

        def changed(_e=None, options=options, field=field, var=var):
            val = next(v for v, n in options if n == var.get())
            self.m.update_settings(**{field: val})
        box.bind("<<ComboboxSelected>>", changed)

    def slider(self, label: str, field: str, lo: float, hi: float, step: float | None, percent: bool,
               ends: tuple[str, str] | None = None) -> None:
        row = self.p._row(self.parent, label)
        var = tk.DoubleVar(value=getattr(self.m.settings, field))
        if ends:
            ttk.Label(row, text=ends[0], foreground="gray").pack(side="left", padx=(0, px(6)))
        shown = ttk.Label(row, text="", foreground="gray", width=5)

        def moved(v):
            x = float(v)
            if step:
                x = round(x / step) * step
            self.m.update_settings(**{field: x})
            shown["text"] = f"{round(x * 100)}%"
        sc = ttk.Scale(row, from_=lo, to=hi, variable=var, command=moved, length=px(200))
        sc.pack(side="left")
        if ends:
            ttk.Label(row, text=ends[1], foreground="gray").pack(side="left", padx=(px(6), 0))
        if percent:
            shown["text"] = f"{round(var.get() * 100)}%"
            shown.pack(side="left", padx=(px(8), 0))
        if self.state == "disabled":
            sc.state(["disabled"])

    def check(self, field: str, text: str) -> None:
        row = self.p._row(self.parent, "")
        var = tk.BooleanVar(value=getattr(self.m.settings, field))
        cb = ttk.Checkbutton(row, text=text, variable=var,
                             command=lambda: self.m.update_settings(**{field: var.get()}))
        cb.pack(anchor="w")
        if self.state == "disabled":
            cb.state(["disabled"])

    # -- sections
    def audio(self) -> None:
        t = self.t
        if t.is_lossy_audio:
            self.menu("Bitrate", "audio_bitrate",
                      [(b, "VBR – best (~245 kbps)" if b == 0 else f"{b} kbps") for b in t.bitrate_options])
        if t.is_lossless_audio:
            self.menu("Bit depth", "bit_depth", [(0, "Match source"), (16, "16-bit"), (24, "24-bit")])
        if t.sample_rates:
            self.menu("Sample rate", "sample_rate",
                      [(r, "Same as source" if r == 0 else rate_label(r)) for r in t.sample_rates])
        if t.has_channel_option:
            self.menu("Channels", "channels", [(0, "Same as source"), (2, "Stereo"), (1, "Mono")])

    def video(self) -> None:
        if self.t.id != "mov-prores":
            self.slider("Quality", "video_quality", 0, 1, None, False, ("Smaller", "Better"))
        self.menu("Resolution", "video_height", [(0, "Same as source"), (2160, "4K (2160p)"), (1440, "1440p"),
                                                 (1080, "1080p"), (720, "720p"), (480, "480p")])
        self.menu("Frame rate", "fps", [(0, "Same as source"), (60, "60 fps"), (30, "30 fps"),
                                        (25, "25 fps"), (24, "24 fps")])
        self.check("keep_audio", "Include audio")
        if self.t.has_hardware_option and self.p.app.caps_ready.is_set() and caps.hw_available():
            self.check("hardware", "Hardware encoding (GPU, much faster)")

    def gif(self) -> None:
        self.menu("Width", "gif_width", [(w, f"{w} px") for w in (320, 480, 640, 800, 1080)])
        self.menu("Frame rate", "gif_fps", [(f, f"{f} fps") for f in (10, 12, 15, 20, 25, 30)])

    def image(self) -> None:
        if self.t.is_lossy_image:
            self.slider("Quality", "image_quality", 0.3, 1, 0.05, True)
        self.menu("Max size", "max_size", [(0, "Same as source"), (4096, "4096 px"), (2048, "2048 px"),
                                           (1920, "1920 px"), (1280, "1280 px"), (1080, "1080 px"), (512, "512 px")])

    def document(self) -> None:
        if self.t.id == "pdf-doc":
            self.menu("Paper", "paper_a4", [(True, "A4"), (False, "US Letter")])
            row = self.p._row(self.parent, "")
            ttk.Label(row, text="Applies to text, Markdown and HTML. Word files keep their own page size.",
                      foreground="gray", wraplength=px(300), justify="left").pack(anchor="w")

    def pdf(self) -> None:
        if self.t.id != "pdf-txt":
            self.menu("Resolution", "pdf_dpi", [(72, "72 dpi (screen)"), (150, "150 dpi"), (300, "300 dpi (print)")])
            if self.t.id == "pdf-jpg":
                self.slider("Quality", "image_quality", 0.3, 1, 0.05, True)
