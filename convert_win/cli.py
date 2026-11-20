"""Test mode: `python -m convert_win --cli <target-id> file…` converts with default settings."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from . import converter
from .formats import ConvSettings, Targets
from .tools import Runner


def run(args: list[str]) -> int:
    t = Targets.by_id(args[0]) if args else None
    if not t or len(args) < 2:
        print("usage: python -m convert_win --cli <target> file…\ntargets: " + " ".join(x.id for x in Targets.all()))
        return 2
    s = ConvSettings()
    if os.environ.get("CONVERT_SETTINGS"):
        s = ConvSettings.from_dict(json.loads(os.environ["CONVERT_SETTINGS"]))
    failed = False
    for f in args[1:]:
        p = Path(f)
        start = time.time()
        try:
            outs = converter.convert(p, t, s, Runner())
            print(f"OK   {p.name} -> {', '.join(o.name for o in outs)} ({time.time() - start:.2f}s)")
        except Exception as e:
            failed = True
            print(f"FAIL {p.name}: {e}")
    return 1 if failed else 0
