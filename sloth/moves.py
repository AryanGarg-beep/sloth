"""Moving files safely, and the journal that makes every move undoable.

Rules: never delete, never overwrite. A move either lands on a name nobody is
using or doesn't happen. Name clashes get " (2)", " (3)", ...
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import secrets
import shutil
import time
from pathlib import Path

from .cache import sha256

# --- no-clobber rename ------------------------------------------------------------

_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
try:
    _libc = ctypes.CDLL(None, use_errno=True)
    _renameat2 = _libc.renameat2
    _renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    _renameat2.restype = ctypes.c_int
except (OSError, AttributeError):
    _renameat2 = None


def rename_noreplace(src: Path, dst: Path) -> None:
    """Atomically rename src to dst, failing with FileExistsError if dst exists.

    Uses Linux renameat2(RENAME_NOREPLACE). Where that isn't supported, falls back
    to link + unlink, which is also no-clobber. Raises OSError(EXDEV) across
    filesystems.
    """
    if _renameat2 is not None:
        if _renameat2(_AT_FDCWD, os.fsencode(src), _AT_FDCWD, os.fsencode(dst), _RENAME_NOREPLACE) == 0:
            return
        err = ctypes.get_errno()
        if err not in (errno.EINVAL, errno.ENOSYS, errno.ENOTSUP):
            raise OSError(err, os.strerror(err), str(src), None, str(dst))
    os.link(src, dst)
    os.unlink(src)


def clash_name(name: str, n: int) -> str:
    """"notes.pdf", 2 -> "notes (2).pdf"."""
    if n == 1:
        return name
    p = Path(name)
    return f"{p.stem} ({n}){p.suffix}"


def free_name(folder: Path, name: str) -> Path:
    """The first of name, name (2), name (3)... that doesn't exist yet."""
    n = 1
    while (folder / clash_name(name, n)).exists() or (folder / clash_name(name, n)).is_symlink():
        n += 1
    return folder / clash_name(name, n)


def make_dirs(folder: Path) -> list[str]:
    """mkdir -p, returning the directories that were actually created (top first)."""
    missing = []
    p = folder
    while not p.exists():
        missing.append(p)
        p = p.parent
    created = []
    for d in reversed(missing):
        try:
            d.mkdir()
            created.append(str(d))
        except FileExistsError:
            pass
    return created


def safe_move(src: Path, folder: Path, name: str | None = None) -> Path:
    """Move src into folder without overwriting anything. Returns the final path."""
    name = name or src.name
    for n in range(1, 10_000):
        dst = folder / clash_name(name, n)
        if dst.exists() or dst.is_symlink():
            continue
        try:
            rename_noreplace(src, dst)
            return dst
        except FileExistsError:
            continue  # someone took the name between our check and the rename
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
            return _copy_move(src, folder, name)
    raise OSError(f"no free name for {name} in {folder}")


def _copy_move(src: Path, folder: Path, name: str) -> Path:
    """Across filesystems: copy, verify, put in place without clobbering, then remove the source."""
    tmp = folder / f".sloth-{secrets.token_hex(4)}-{name}"
    shutil.copy2(src, tmp)
    try:
        if sha256(tmp) != sha256(src):
            raise OSError(f"copy of {src} did not verify")
        for n in range(1, 10_000):
            dst = folder / clash_name(name, n)
            try:
                rename_noreplace(tmp, dst)
                break
            except FileExistsError:
                continue
        else:
            raise OSError(f"no free name for {name} in {folder}")
    except BaseException:
        tmp.unlink(missing_ok=True)  # our own temporary copy, never the original
        raise
    src.unlink()  # only now that a verified copy is in place
    return dst


# --- journal -----------------------------------------------------------------------

class Journal:
    """Append-only JSONL log of batches and moves.

    Each move is written as "pending" before it happens and "done" after, so an
    interrupted run can always be reconstructed.
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict) -> None:
        record = {"ts": time.time(), **record}
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
        return out

    def batches(self) -> dict[str, dict]:
        """batch id -> {"start", "moves" (done), "pending", "undone", "undone_files"}"""
        out: dict[str, dict] = {}
        for r in self.records():
            b = out.setdefault(r.get("batch", ""), {"start": None, "moves": [], "pending": {}, "undone": False,
                                                     "undone_files": set()})
            kind = r["type"]
            if kind == "batch_start":
                b["start"] = r
            elif kind == "move" and r["state"] == "pending":
                b["pending"][r["src"]] = r
            elif kind == "move" and r["state"] == "done":
                b["pending"].pop(r["src"], None)
                b["moves"].append(r)
            elif kind == "undo" and r["state"] == "done":
                b["undone_files"].add(r["dst"])
            elif kind == "undo_end" and r.get("complete"):
                b["undone"] = True
        return out


def new_batch_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
