"""State and batch logic behind one conversion window (ConvertModel.swift). No GUI code in here."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from . import converter, docs
from .formats import ConvSettings, MediaKind, OutGroup, Target, Targets
from .tools import ConvertError, Runner, Store, Tool

IDLE, RUNNING, DONE = "idle", "running", "done"


def send_to_recycle_bin(path: Path) -> None:
    from send2trash import send2trash
    send2trash(str(path))


class ConvertModel:
    def __init__(self, files: list[Path], store: Store | None = None,
                 trash_fn: Callable[[Path], None] = send_to_recycle_bin) -> None:
        self.store = store or Store()
        self.trash_fn = trash_fn
        self.files: list[Path] = []
        self.skipped = 0
        self.target_id = ""
        self.settings = ConvSettings()
        self.phase = IDLE
        self.progress = 0.0
        self.current_name = ""
        self.outputs: list[Path] = []
        self.errors: list[str] = []
        self.stopped = False
        self.trashed = 0
        self.trash_originals: bool = bool(self.store.get("trashOriginals", False))
        self.on_change: Callable[[], None] = lambda: None        # GUI hooks (called from any thread)
        self._runner = Runner()
        self._lock = threading.Lock()
        self.add(files)

    # ------------------------------------------------------------ derived
    @property
    def kinds(self) -> set[MediaKind]:
        return {k for k in (MediaKind.of(f) for f in self.files) if k}

    @property
    def groups(self): return Targets.groups(self.kinds)

    @property
    def target(self) -> Optional[Target]: return Targets.by_id(self.target_id)

    @property
    def primary_kind(self) -> Optional[MediaKind]:
        return next(iter(self.kinds)) if len(self.kinds) == 1 else None

    @property
    def group_key(self) -> str: return "+".join(sorted(k.value for k in self.kinds))

    def hint(self) -> Optional[str]:
        """A reason the chosen conversion can't run on this machine yet, if any."""
        t = self.target
        if not t:
            return None
        if t.group in (OutGroup.audio, OutGroup.video) and not Tool.ffmpeg():
            return "Needs ffmpeg (winget install Gyan.FFmpeg)"
        if t.group == OutGroup.document:
            exts = {f.suffix.lower().lstrip(".") for f in self.files}
            return docs.requirement_hint(exts, t)
        return None

    # ------------------------------------------------------------ files
    def add(self, paths: list[Path]) -> None:
        if self.phase == RUNNING:
            return
        if self.phase == DONE:
            self._reset()
        for p in paths:
            p = Path(p)
            if p in self.files:
                continue
            if p.is_file() and MediaKind.of(p):
                self.files.append(p)
            else:
                self.skipped += 1
        self._choose_target()
        self.on_change()

    def remove(self, p: Path) -> None:
        if self.phase != IDLE:
            return
        self.files = [f for f in self.files if f != p]
        self._choose_target()
        self.on_change()

    def _reset(self) -> None:
        self.phase = IDLE; self.outputs = []; self.errors = []; self.progress = 0.0
        self.stopped = False; self.trashed = 0

    def _choose_target(self) -> None:
        available = [t for _, ts in self.groups for t in ts]
        if not available:
            self.target_id = ""
            return
        if any(t.id == self.target_id for t in available):
            return
        last = self.store.get(f"lastTarget.{self.group_key}")
        exts = {f.suffix.lower().lstrip(".") for f in self.files}
        pick = (next((t for t in available if t.id == last), None)
                or Targets.preferred(self.kinds, exts, available) or available[0])
        self.set_target(pick.id)

    def set_target(self, tid: str) -> None:
        self.target_id = tid
        t = self.target
        if not t:
            return
        self.store.set(f"lastTarget.{self.group_key}", t.id)
        s = ConvSettings()
        saved = self.store.get(f"settings.{t.id}")
        if isinstance(saved, dict):
            s = ConvSettings.from_dict(saved)
        if t.bitrate_options and s.audio_bitrate not in t.bitrate_options:
            s.audio_bitrate = t.default_bitrate
        if s.sample_rate not in t.sample_rates:
            s.sample_rate = 0
        self.settings = s

    def update_settings(self, **changes) -> None:
        for k, v in changes.items():
            setattr(self.settings, k, v)
        t = self.target
        if t:
            self.store.set(f"settings.{t.id}", self.settings.to_dict())

    def set_trash_originals(self, on: bool) -> None:
        self.trash_originals = on
        self.store.set("trashOriginals", on)

    # ------------------------------------------------------------ running
    def start(self) -> None:
        t = self.target
        if not t or self.phase != IDLE or not self.files:
            return
        self._reset()
        self.phase = RUNNING
        self._runner = Runner()
        threading.Thread(target=self._work, args=(t, list(self.files), self.settings, self._runner,
                                                  self.trash_originals), daemon=True).start()
        self.on_change()

    def _work(self, t: Target, files: list[Path], s: ConvSettings, runner: Runner, trash: bool) -> None:
        n = len(files)
        for i, f in enumerate(files):
            if self.stopped:
                break
            self.current_name = f.name
            self.progress = i / n

            def report(p: float, i=i) -> None:
                if self.phase == RUNNING:
                    self.progress = (i + p) / n
                    self.on_change()
            self.on_change()
            try:
                made = converter.convert(f, t, s, runner, report)
                self.outputs += made
                # Only after the separate output exists; the Recycle Bin keeps it recoverable.
                if trash and not self.stopped and made and f not in made and all(m.exists() for m in made):
                    try:
                        self.trash_fn(f)
                        self.trashed += 1
                    except Exception:
                        self.errors.append(f"{f.name}: converted, but couldn't move it to the Recycle Bin")
            except ConvertError as e:
                if not self.stopped:
                    self.errors.append(f"{f.name}: {e}")
            except Exception as e:                      # never let a bug leave the window stuck on "running"
                if not self.stopped:
                    self.errors.append(f"{f.name}: {e.__class__.__name__}: {e}")
        self.progress = 1.0
        self.phase = DONE
        self.on_change()

    def cancel(self) -> None:
        if self.phase != RUNNING:
            return
        self.stopped = True
        self._runner.cancel()
