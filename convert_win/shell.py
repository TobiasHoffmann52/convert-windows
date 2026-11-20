"""Explorer right-click entry (replaces QuickAction.swift's Automator workflow).

Adds a "Convert…" verb under HKCU\\Software\\Classes\\SystemFileAssociations\\.<ext>\\shell for every
supported extension. HKCU needs no administrator rights, and removing the keys removes the menu item.
On Windows 11 classic verbs live under "Show more options" (Shift+F10 opens that menu directly).
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from .formats import ALL_INPUT_EXTS
from .tools import IS_WINDOWS, ConvertError, app_dir

VERB = "Convert"
LABEL = "Convert…"
BASE = r"Software\Classes\SystemFileAssociations\.{ext}\shell\Convert"


def launch_command() -> str:
    """Command line Explorer runs; %1 is the clicked file."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"'
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe" and (exe.with_name("pythonw.exe")).exists():
        exe = exe.with_name("pythonw.exe")                      # no console window flashing up
    return f'"{exe}" -m convert_win "%1"'


def icon_path() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}",0'
    ico = Path(__file__).resolve().parent / "assets" / "icon.ico"
    return str(ico) if ico.exists() else ""


class _Winreg:
    """Thin wrapper so tests can substitute a fake registry."""

    def __init__(self, module=None):
        if module is None:
            import winreg as module
        self.m = module

    def set_value(self, path: str, name: str, value: str) -> None:
        with self.m.CreateKeyEx(self.m.HKEY_CURRENT_USER, path, 0, self.m.KEY_SET_VALUE) as k:
            self.m.SetValueEx(k, name, 0, self.m.REG_SZ, value)

    def delete_tree(self, path: str) -> None:
        m = self.m
        try:
            with m.OpenKey(m.HKEY_CURRENT_USER, path, 0, m.KEY_READ | m.KEY_WRITE) as k:
                while True:
                    try:
                        sub = m.EnumKey(k, 0)
                    except OSError:
                        break
                    self.delete_tree(path + "\\" + sub)
            m.DeleteKey(m.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass

    def exists(self, path: str) -> bool:
        try:
            with self.m.OpenKey(self.m.HKEY_CURRENT_USER, path):
                return True
        except OSError:
            return False


def _notify_shell() -> None:
    if IS_WINDOWS:
        try:
            ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)   # SHCNE_ASSOCCHANGED
        except Exception:
            pass


def is_installed(reg: _Winreg | None = None) -> bool:
    if not IS_WINDOWS and reg is None:
        return False
    reg = reg or _Winreg()
    return reg.exists(BASE.format(ext="mp3") + "\\command")


def install(reg: _Winreg | None = None, command: str | None = None, icon: str | None = None) -> int:
    if not IS_WINDOWS and reg is None:
        raise ConvertError("The right-click menu can only be installed on Windows")
    reg = reg or _Winreg()
    command = command or launch_command()
    icon = icon_path() if icon is None else icon
    n = 0
    for ext in ALL_INPUT_EXTS:
        base = BASE.format(ext=ext)
        reg.set_value(base, "", LABEL)
        if icon:
            reg.set_value(base, "Icon", icon)
        # Explorer starts one process per selected file; the app merges them into a single window.
        reg.set_value(base, "MultiSelectModel", "Document")
        reg.set_value(base + r"\command", "", command)
        n += 1
    _notify_shell()
    return n


def uninstall(reg: _Winreg | None = None) -> None:
    if not IS_WINDOWS and reg is None:
        return
    reg = reg or _Winreg()
    for ext in ALL_INPUT_EXTS:
        reg.delete_tree(BASE.format(ext=ext))
    _notify_shell()
