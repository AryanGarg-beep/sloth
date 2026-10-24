"""Config and state locations.

Config lives in ~/.config/sloth/config.toml, state (registry, cache, journal,
plans) in ~/.local/share/sloth/. Both can be redirected with SLOTH_CONFIG and
SLOTH_HOME, which the tests use so they never see the real ones.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

HOME = Path.home()
# Folders sloth must not scan or write into unless allow_real_folders is set.
REAL_FOLDERS = (HOME / "Downloads", HOME / "Documents")

SANDBOX = HOME / "sloth_sandbox"

DEFAULT_CONFIG = f"""\
# sloth configuration.
# Until you deliberately switch to your real folders, everything points at a sandbox.

# Folders `sloth plan` scans when you don't name any.
watched = ["{SANDBOX / 'inbox'}"]

# Where sorted files go: <target_root>/<Subject>/Unit <N>/<filename>
target_root = "{SANDBOX / 'College'}"

# Safety latch. While false, sloth refuses to touch ~/Downloads and ~/Documents.
allow_real_folders = false

[model]
name = "gemma4:e4b-it-qat"
host = "http://127.0.0.1:11434"

[classify]
# Below this confidence a file goes to "_Needs a look" instead of being sorted.
confidence_threshold = 0.7

[extract]
max_pages = 3
max_slides = 8
max_chars = 3000
"""


def config_path() -> Path:
    return Path(os.environ.get("SLOTH_CONFIG", HOME / ".config/sloth/config.toml"))


def state_dir() -> Path:
    return Path(os.environ.get("SLOTH_HOME", HOME / ".local/share/sloth"))


@dataclass
class Config:
    watched: list[Path]
    target_root: Path
    allow_real_folders: bool = False
    model: str = "gemma4:e4b-it-qat"
    host: str = "http://127.0.0.1:11434"
    confidence_threshold: float = 0.7
    max_pages: int = 3
    max_slides: int = 8
    max_chars: int = 3000
    state: Path = field(default_factory=state_dir)

    @property
    def registry_path(self) -> Path:
        return self.state / "subjects.json"

    @property
    def cache_path(self) -> Path:
        return self.state / "cache.sqlite"

    @property
    def journal_path(self) -> Path:
        return self.state / "journal.jsonl"

    @property
    def plans_dir(self) -> Path:
        return self.state / "plans"


def ensure_config() -> Path:
    """Write the default (sandbox) config if none exists. Never overwrites."""
    path = config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG)
    return path


def _expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p)))


def load() -> Config:
    data = tomllib.loads(ensure_config().read_text())
    model = data.get("model", {})
    classify = data.get("classify", {})
    extract = data.get("extract", {})
    cfg = Config(
        watched=[_expand(p) for p in data.get("watched", [])],
        target_root=_expand(data["target_root"]),
        allow_real_folders=bool(data.get("allow_real_folders", False)),
        model=model.get("name", Config.model),
        host=model.get("host", Config.host),
        confidence_threshold=float(classify.get("confidence_threshold", Config.confidence_threshold)),
        max_pages=int(extract.get("max_pages", Config.max_pages)),
        max_slides=int(extract.get("max_slides", Config.max_slides)),
        max_chars=int(extract.get("max_chars", Config.max_chars)),
    )
    cfg.state.mkdir(parents=True, exist_ok=True)
    return cfg


def is_real_folder(path: Path) -> bool:
    """True if path is ~/Downloads, ~/Documents or anything inside them."""
    p = path.expanduser().resolve()
    return any(p == r or r in p.parents for r in REAL_FOLDERS)


def check_allowed(cfg: Config, path: Path) -> None:
    if is_real_folder(path) and not cfg.allow_real_folders:
        raise PermissionError(
            f"{path} is inside your real Downloads/Documents. "
            "Set allow_real_folders = true in the config once you're ready."
        )


def save_folders(watched: list[Path], target_root: Path) -> None:
    """Rewrite the watched and target_root lines of the config file, keeping everything else."""
    path = ensure_config()
    text = path.read_text()
    q = lambda p: json.dumps(str(p), ensure_ascii=False)  # noqa: E731 - a JSON string is a valid TOML string
    new = re.sub(r"(?m)^watched\s*=\s*\[[^\]]*\]",
                 lambda m: "watched = [" + ", ".join(q(p) for p in watched) + "]", text, count=1)
    new = re.sub(r'(?m)^target_root\s*=\s*"[^"\n]*"', lambda m: "target_root = " + q(target_root), new, count=1)
    data = tomllib.loads(new)
    if [_expand(p) for p in data.get("watched", [])] != list(watched) or _expand(data["target_root"]) != target_root:
        raise ValueError(f"couldn't update {path} safely; edit watched/target_root there by hand")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new)
    os.replace(tmp, path)
