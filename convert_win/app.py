"""Program entry: argument handling, single-instance merge, Tk application (AppDelegate.swift)."""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from . import gui, ipc, shell
from .tools import IS_WINDOWS, Store


def _windows_setup() -> None:
    if not IS_WINDOWS:
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)         # crisp text on high-DPI screens
    except Exception:
        pass
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("local.convert.app")
    except Exception:
        pass


def _system_is_dark() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


class App:
    def __init__(self, listener: ipc.Listener | None) -> None:
        try:
            from tkinterdnd2 import DND_FILES, TkinterDnD
            self.root = TkinterDnD.Tk()
            self.dnd, self.dnd_files = True, DND_FILES
        except Exception:
            import tkinter as tk
            self.root = tk.Tk()
            self.dnd, self.dnd_files = False, None
        self.root.withdraw()
        self.root.title("Convert")
        forced = os.environ.get("CONVERT_TK_SCALING")           # debugging aid: pretend to be a high-DPI screen
        if forced:
            self.root.tk.call("tk", "scaling", float(forced))
        gui.SCALE = max(1.0, float(self.root.tk.call("tk", "scaling")) / (96 / 72))
        self.dark = _system_is_dark()
        try:
            import sv_ttk
            sv_ttk.set_theme("dark" if self.dark else "light")
        except Exception:
            pass
        self.store = Store()
        self.text_scale = min(2.0, max(1.0, float(self.store.get("textScale", 1.0) or 1.0)))
        gui.apply_fonts(self.root, self.text_scale)
        from tkinter import ttk
        self.bg = ttk.Style().lookup("TFrame", "background") or ("#202020" if self.dark else "#fafafa")
        self.panels: list[gui.Panel] = []
        self.listener = listener
        self.caps_ready = threading.Event()
        threading.Thread(target=self._warm_caps, daemon=True).start()   # probe GPU encoders off the UI thread
        self.root.after(150, self._poll)

    def _warm_caps(self) -> None:
        from . import caps
        try:
            caps.hw_available()
        finally:
            self.caps_ready.set()

    def open_panel(self, files: list[Path]) -> None:
        # Late arrivals from a multi-file right-click join the window that was just opened.
        recent = self.panels[-1] if self.panels else None
        if files and recent and recent.model.phase == "idle" and time.time() - recent.created < 3.0:
            recent.model.add(files)
            recent.refresh()
            recent.win.lift()
            return
        for p in self.panels:                                  # reuse an empty drop window
            if files and not p.model.files and p.model.phase == "idle":
                p.model.add(files); p.refresh(); p.win.lift(); p.win.focus_force()
                return
        if not files and self.panels:
            self.panels[-1].win.deiconify(); self.panels[-1].win.lift()
            return
        self.panels.append(gui.Panel(self, files))

    def panel_closed(self, panel) -> None:
        if panel in self.panels:
            self.panels.remove(panel)
        if not self.panels:
            self.root.after(50, self.quit)

    def set_text_scale(self, value: float) -> None:
        value = min(2.0, max(1.0, float(value)))
        self.text_scale = value
        self.store.set("textScale", value)
        gui.apply_fonts(self.root, value)
        for p in self.panels:
            p.text_var.set(value)
        self.refresh_all()
        for p in self.panels:
            p.win.geometry("")                                   # let the window grow or shrink to fit

    def step_text_scale(self, direction: int) -> None:
        sizes = gui.TEXT_SIZES
        i = min(range(len(sizes)), key=lambda k: abs(sizes[k] - self.text_scale))
        self.set_text_scale(sizes[max(0, min(len(sizes) - 1, i + direction))])

    def refresh_all(self) -> None:
        for p in self.panels:
            p._sig = None
            p.refresh()

    def quit(self) -> None:
        if self.listener:
            self.listener.close()
        self.root.quit()
        self.root.destroy()

    def _poll(self) -> None:
        if self.listener:
            try:
                while True:
                    files = self.listener.inbox.get_nowait()
                    self.open_panel([Path(f) for f in files])
            except Exception:
                pass
        if self.caps_ready.is_set() and not getattr(self, "_caps_refreshed", False):
            self._caps_refreshed = True
            self.refresh_all()
        for p in list(self.panels):
            if p.model.phase == "running":
                p._tick()
        self.root.after(150, self._poll)

    def run(self, files: list[Path]) -> None:
        self.open_panel(files)
        self.root.mainloop()


def _message(text: str) -> None:
    if sys.stdout is not None and sys.stdout.isatty():
        print(text)
        return
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk(); r.withdraw(); messagebox.showinfo("Convert", text); r.destroy()
    except Exception:
        print(text)


def selftest() -> int:
    """Used by the build pipeline: proves the packaged app has all its libraries, assets and Tk drag-and-drop."""
    import tempfile
    report: list[str] = []
    bad = 0

    def check(name, fn):
        nonlocal bad
        try:
            fn()
            report.append(f"ok    {name}")
        except Exception as e:                         # noqa: BLE001
            bad += 1
            report.append(f"FAIL  {name}: {e.__class__.__name__}: {e}")

    def imports():
        import PIL, pillow_heif, pypdfium2, markdown_it, mammoth, send2trash, sv_ttk, tkinterdnd2  # noqa: F401
        from mdit_py_plugins.tasklists import tasklists_plugin  # noqa: F401

    def assets():
        a = Path(gui.__file__).parent / "assets"
        assert (a / "icon.ico").is_file() and (a / "icon.png").is_file(), f"missing in {a}"

    def tk_and_dnd():
        from tkinterdnd2 import TkinterDnD
        import sv_ttk
        r = TkinterDnD.Tk(); r.withdraw()
        try:
            r.tk.call("package", "require", "tkdnd")
            sv_ttk.set_theme("light")
        finally:
            r.destroy()

    def conversions():
        from PIL import Image
        from . import converter, markdown_render
        from .formats import ConvSettings, Targets
        from .tools import Runner
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            Image.new("RGB", (64, 48), "orange").save(d / "a.png")
            for tid in ("jpg", "heic", "webp", "pdf-img"):
                t = Targets.by_id(tid)
                assert t, f"{tid} unavailable"
                assert converter.convert(d / "a.png", t, ConvSettings(), Runner())[0].exists(), tid
            outs = converter.convert(d / "a.pdf", Targets.by_id("pdf-png"), ConvSettings(), Runner())
            assert outs[0].exists()
            assert "<h1>" in markdown_render.to_html_body("# hi")

    check("libraries", imports)
    check("assets", assets)
    check("tk + drag and drop + theme", tk_and_dnd)
    check("image / PDF / markdown conversion", conversions)
    check("right-click command", lambda: shell.launch_command().count("%1") == 1 or 1 / 0)
    text = f"Convert {__import__('convert_win').__version__} self-test\n" + "\n".join(report) + "\n"
    try:
        (Path(tempfile.gettempdir()) / "convert-selftest.txt").write_text(text, encoding="utf-8")
    except OSError:
        pass
    if sys.stdout is not None:
        print(text)
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--cli"]:
        from .cli import run
        sys.exit(run(argv[1:]))
    if argv[:1] == ["--selftest"]:
        sys.exit(selftest())
    if argv[:1] == ["--version"]:
        from . import __version__
        _message(f"Convert for Windows {__version__}")
        return
    if argv[:1] == ["--install-context-menu"]:
        n = shell.install()
        _message(f"Right-click menu added for {n} file types.")
        return
    if argv[:1] == ["--uninstall-context-menu"]:
        shell.uninstall()
        _message("Right-click menu removed.")
        return
    _windows_setup()
    files = [a for a in argv if not a.startswith("--")]

    # Already running? Hand the files over and exit. Otherwise become the instance that collects them.
    if ipc.send(files):
        return
    listener = ipc.Listener()
    if not listener.start():
        time.sleep(0.15)                       # lost a race with a sibling that has just started listening
        if ipc.send(files):
            return
        listener = None
    elif files:
        files += listener.collect()            # Explorer launches one copy per selected file
        files.sort(key=lambda f: [int(t) if t.isdigit() else t.lower() for t in __import__('re').split(r'(\d+)', Path(f).name)])
    app = App(listener)
    app.run([Path(f) for f in files])
