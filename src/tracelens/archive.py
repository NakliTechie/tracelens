"""Incremental copy of the live transcript trees into archive/<agent>/.

A file is copied when the archive has no file at that relative path, or the
archived copy differs in size or mtime. Copies keep the source mtime, so a
re-run with unchanged sources copies 0 files. Files that vanish from the
source (Claude Code's cleanupPeriodDays sweep) stay in the archive.

Each copy goes to a temp name and is renamed into place, so an interrupted
run leaves the old copy or the new one, never a partial file. On APFS the
copy is a clonefile(2): copy-on-write, no extra disk until the source changes.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

_libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True) if sys.platform == "darwin" else None


def _copy(src: Path, dst: Path) -> None:
    tmp = dst.with_name(f".{dst.name}.tmp")
    tmp.unlink(missing_ok=True)
    if _libc is None or _libc.clonefile(os.fsencode(src), os.fsencode(tmp), 0) != 0:
        shutil.copyfile(src, tmp)
    st = src.stat()
    os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
    os.replace(tmp, dst)


@dataclass
class ArchiveResult:
    agent: str
    scanned: int = 0
    copied: int = 0
    bytes_copied: int = 0
    errors: list[str] = field(default_factory=list)


def archive_tree(agent: str, src: Path, dst_root: Path) -> ArchiveResult:
    res = ArchiveResult(agent)
    if not src.is_dir():
        res.errors.append(f"source missing: {src}")
        return res
    for s in src.rglob("*.jsonl"):
        res.scanned += 1
        d = dst_root / s.relative_to(src)
        try:
            st = s.stat()
            try:
                dt = d.stat()
                if dt.st_size == st.st_size and dt.st_mtime_ns == st.st_mtime_ns:
                    continue
            except FileNotFoundError:
                d.parent.mkdir(parents=True, exist_ok=True)
            _copy(s, d)
            res.copied += 1
            res.bytes_copied += st.st_size
        except OSError as e:  # a file rotated away mid-scan; report, keep going
            res.errors.append(f"{s}: {e}")
    return res
