"""Find candidate files in the watched folders."""

from __future__ import annotations

from pathlib import Path

from . import config

# Downloads still in progress, and editor lock / temp files.
PARTIAL_SUFFIXES = {".crdownload", ".part", ".partial", ".download", ".tmp", ".opdownload", ".!qb"}


def skip_reason(path: Path) -> str | None:
    name = path.name
    if name.startswith("."):
        return "hidden"
    if name.startswith("~$") or name.startswith(".~lock."):
        return "office lock file"
    if path.suffix.lower() in PARTIAL_SUFFIXES:
        return "partial download"
    if path.is_symlink():
        return "symlink"
    if not path.is_file():
        return "not a regular file"
    return None


def scan(folders: list[Path], cfg: config.Config, recursive: bool = False) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Return (files to consider, [(path, why skipped)]). Never follows symlinked dirs."""
    target = cfg.target_root.expanduser().resolve()
    files, skipped = [], []
    for folder in folders:
        folder = folder.expanduser()
        config.check_allowed(cfg, folder)
        if not folder.is_dir():
            skipped.append((folder, "folder not found"))
            continue
        stack = [folder]
        while stack:
            d = stack.pop()
            try:
                entries = sorted(d.iterdir(), key=lambda p: p.name.casefold())
            except PermissionError:
                skipped.append((d, "permission denied"))
                continue
            for p in entries:
                resolved = p.resolve()
                if resolved == target or target in resolved.parents:
                    continue  # already sorted
                if p.is_dir() and not p.is_symlink():
                    if recursive and not p.name.startswith("."):
                        stack.append(p)
                    continue
                why = skip_reason(p)
                if why:
                    skipped.append((p, why))
                else:
                    files.append(p)
    return files, skipped
