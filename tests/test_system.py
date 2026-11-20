import json
import sys
import threading
import time
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

from convert_win import ipc, shell
from convert_win.formats import ALL_INPUT_EXTS
from convert_win.model import DONE, ConvertModel
from convert_win.tools import Store


# ---------------------------------------------------------------- a faithful in-memory winreg
class FakeWinreg(types.SimpleNamespace):
    def __init__(self):
        self.tree = {}
        super().__init__(HKEY_CURRENT_USER="HKCU", KEY_SET_VALUE=2, KEY_READ=1, KEY_WRITE=2, REG_SZ=1)

    @contextmanager
    def CreateKeyEx(self, root, path, res, access):
        node = self.tree
        for part in path.split("\\"):
            node = node.setdefault(part, {"__values__": {}})
        yield node

    def SetValueEx(self, key, name, res, typ, value):
        key["__values__"][name] = value

    def _walk(self, path):
        node = self.tree
        for part in path.split("\\"):
            if part not in node:
                raise FileNotFoundError(path)
            node = node[part]
        return node

    @contextmanager
    def OpenKey(self, root, path, res=0, access=0):
        yield self._walk(path)

    def EnumKey(self, key, i):
        subs = [k for k in key if k != "__values__"]
        if i >= len(subs):
            raise OSError("no more")
        return subs[i]

    def DeleteKey(self, root, path):
        parts = path.split("\\")
        parent = self._walk("\\".join(parts[:-1])) if len(parts) > 1 else self.tree
        node = parent[parts[-1]]
        if any(k != "__values__" for k in node):
            raise OSError("key has subkeys")           # real Windows refuses this too
        del parent[parts[-1]]


def test_install_registers_a_verb_per_extension():
    fake = FakeWinreg(); reg = shell._Winreg(fake)
    n = shell.install(reg, command='"C:\\Convert\\Convert.exe" "%1"', icon="C:\\Convert\\icon.ico")
    assert n == len(ALL_INPUT_EXTS) > 50
    key = r"Software\Classes\SystemFileAssociations\.mp3\shell\Convert"
    node = fake._walk(key)
    assert node["__values__"][""] == "Convert…"
    assert node["__values__"]["Icon"].endswith("icon.ico")
    assert node["__values__"]["MultiSelectModel"] == "Document"
    assert fake._walk(key + r"\command")["__values__"][""] == '"C:\\Convert\\Convert.exe" "%1"'
    assert shell.is_installed(reg)


def test_uninstall_removes_everything_it_added_and_nothing_else():
    fake = FakeWinreg(); reg = shell._Winreg(fake)
    other = r"Software\Classes\SystemFileAssociations\.mp3\shell\SomeOtherApp"
    with fake.CreateKeyEx("HKCU", other, 0, 0) as k:
        k["__values__"][""] = "keep me"
    shell.install(reg, command="x", icon="")
    shell.uninstall(reg)
    assert not shell.is_installed(reg)
    assert fake._walk(other)["__values__"][""] == "keep me"
    shell.uninstall(reg)                                # idempotent


def test_launch_command_quotes_paths_with_spaces(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Convert\Convert.exe")
    assert shell.launch_command() == r'"C:\Program Files\Convert\Convert.exe" "%1"'


# ---------------------------------------------------------------- one window for many launches
def test_simultaneous_launches_merge_into_one_collector():
    listener = ipc.Listener(0)
    assert listener.start()
    port = listener.port
    results = []

    def child(i):
        results.append(ipc.send([f"C:\\music\\song{i}.mp3"], port=port))
    threads = [threading.Thread(target=child, args=(i,)) for i in range(12)]
    for t in threads: t.start()
    got = listener.collect(quiet=0.4)
    for t in threads: t.join()
    listener.close()
    assert all(results) and sorted(got) == sorted(f"C:\\music\\song{i}.mp3" for i in range(12))


def test_second_listener_cannot_steal_the_port_and_send_fails_when_nobody_listens():
    a = ipc.Listener(0); assert a.start()
    assert not ipc.Listener(a.port).start()
    free = a.port
    a.close()
    assert not ipc.send(["x"], port=free, timeout=0.3)


def test_foreign_service_on_the_port_is_ignored():
    import socket
    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); srv.bind(("127.0.0.1", 0)); srv.listen(1); port = srv.getsockname()[1]
    threading.Thread(target=lambda: (srv.accept()[0].sendall(b"HTTP/1.1 400\r\n\r\n")), daemon=True).start()
    assert not ipc.send(["x"], port=port, timeout=0.5)
    srv.close()


# ---------------------------------------------------------------- the model
def _wait(m):
    t0 = time.time()
    while m.phase != DONE and time.time() - t0 < 60:
        time.sleep(0.03)
    assert m.phase == DONE


def test_originals_go_to_recycle_bin_only_after_success(tone, tmp_path):
    import shutil
    good, bad = tmp_path / "good.wav", tmp_path / "bad.mp3"
    shutil.copy(tone, good); bad.write_bytes(b"junk")
    recycled = []
    m = ConvertModel([good, bad], Store(tmp_path / "s.json"), trash_fn=lambda p: recycled.append(p.name))
    m.set_target("flac"); m.set_trash_originals(True); m.start(); _wait(m)
    assert recycled == ["good.wav"] and m.trashed == 1
    assert len(m.errors) == 1 and m.errors[0].startswith("bad.mp3:")
    assert (tmp_path / "good.flac").exists() and not list(tmp_path.glob("bad*.flac"))


def test_recycle_failure_is_reported_not_fatal(tone, tmp_path):
    import shutil
    f = tmp_path / "a.wav"; shutil.copy(tone, f)
    def boom(p): raise OSError("locked")
    m = ConvertModel([f], Store(tmp_path / "s.json"), trash_fn=boom)
    m.set_target("flac"); m.set_trash_originals(True); m.start(); _wait(m)
    assert m.outputs and "Recycle Bin" in m.errors[0]


def test_settings_and_last_format_are_remembered(tone, tmp_path):
    st = Store(tmp_path / "s.json")
    m = ConvertModel([tone], st); m.set_target("opus"); m.update_settings(audio_bitrate=96)
    m2 = ConvertModel([tone], Store(tmp_path / "s.json"))
    assert m2.target_id == "opus" and m2.settings.audio_bitrate == 96


def test_unsupported_files_are_counted_and_skipped(tone, tmp_path):
    junk = tmp_path / "x.xyz"; junk.write_text("x")
    m = ConvertModel([tone, junk, tmp_path / "missing.wav"], Store(tmp_path / "s.json"))
    assert m.files == [tone] and m.skipped == 2
