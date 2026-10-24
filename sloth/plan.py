"""Build, save, load and show a plan. A plan only describes moves; nothing moves here."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from . import classify as C
from . import config
from .cache import Cache
from .extract import extract_cached
from .ollama import Ollama
from .registry import Registry, safe_folder
from .scan import scan
from .ui import c, table

NEEDS_LOOK = "_Needs a look"
PLAN_VERSION = 1


def unit_dir(unit: int | None) -> str | None:
    return f"Unit {unit}" if unit else None


def dest_dir(item: dict, registry: Registry) -> str | None:
    """Destination folder relative to the target root, or None if the file stays put."""
    status = item["status"]
    if status == C.SORTED:
        subject = registry.get(item["subject"])
        if subject is None:  # renamed or removed since the plan was made
            return NEEDS_LOOK
        parts = [subject.folder, unit_dir(item["unit"])]
    elif status in (C.NEW, C.ASK):
        parts = [safe_folder(item["new_subject"]["name"]), unit_dir(item["unit"])]
    elif status == C.UNSURE:
        parts = [NEEDS_LOOK]
    else:
        return None
    return "/".join(p for p in parts if p)


def make_plan(folders: list[Path], cfg: config.Config, registry: Registry, *,
              recursive: bool = False, fresh: bool = False, progress=None, ollama=None) -> dict:
    files, skipped = scan(folders, cfg, recursive=recursive)
    ollama = ollama or Ollama(cfg.host)
    cache = Cache(cfg.cache_path)
    items = []
    try:
        for i, path in enumerate(files, start=1):
            ex = extract_cached(path, cfg, cache)
            got = C.classify(path, ex, registry, ollama, cfg, cache=None if fresh else cache)
            st = path.stat()
            item = {
                "src": str(path), "name": path.name, "sha256": ex["sha256"], "size": st.st_size,
                "mtime": st.st_mtime, "extract_method": ex["method"], "extract_s": ex["seconds"],
                **{k: got[k] for k in ("status", "subject", "suggestion", "new_subject", "unit", "unit_source",
                                       "unit_note", "doc_type", "confidence", "reason", "model_s", "cached")},
            }
            item["dest_dir"] = dest_dir(item, registry)
            items.append(item)
            if progress:
                progress(i, len(files), item)
    finally:
        cache.close()
    return {
        "version": PLAN_VERSION,
        "id": time.strftime("%Y%m%d-%H%M%S"),
        "created": time.time(),
        "model": cfg.model,
        "target_root": str(cfg.target_root),
        "folders": [str(f) for f in folders],
        "items": items,
        "skipped": [{"path": str(p), "why": why} for p, why in skipped],
        "applied_batch": None,
    }


def save_plan(plan: dict, cfg: config.Config) -> Path:
    cfg.plans_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.plans_dir / f"plan-{plan['id']}.json"
    data = json.dumps(plan, indent=1, ensure_ascii=False)
    path.write_text(data)
    latest = cfg.plans_dir / "latest.json"
    tmp = latest.with_suffix(".tmp")
    tmp.write_text(json.dumps({"path": str(path)}))
    tmp.replace(latest)
    return path


def latest_plan_path(cfg: config.Config) -> Path | None:
    latest = cfg.plans_dir / "latest.json"
    if not latest.exists():
        return None
    return Path(json.loads(latest.read_text())["path"])


def load_plan(path: Path) -> dict:
    return json.loads(path.read_text())


def progress_line(i: int, n: int, item: dict) -> None:
    where = item["dest_dir"] or item["status"].replace("_", " ")
    took = "cached" if item["cached"] else f"{item['extract_s'] + item['model_s']:.1f}s"
    print(c(f"[{i:>{len(str(n))}}/{n}] ", "dim") + f"{item['name']}  →  {where}  " + c(took, "dim"),
          file=sys.stderr, flush=True)


# --- display -------------------------------------------------------------------

def _label(item: dict) -> str:
    s = item["status"]
    if s == C.NEW:
        return c("NEW SUBJECT ", "yellow") + item["dest_dir"]
    if s == C.ASK:
        return c(f"NEW? (did you mean {item['suggestion']}?) ", "yellow") + item["dest_dir"]
    if s == C.UNSURE:
        guess = f" (best guess: {item['subject']})" if item.get("subject") else ""
        return c(NEEDS_LOOK, "yellow") + guess
    if s == C.NOT_COLLEGE:
        return c("left alone: not college", "dim")
    if s == C.UNREADABLE:
        return c("left alone: can't read", "dim")
    return item["dest_dir"]


_ORDER = {C.SORTED: 0, C.NEW: 1, C.ASK: 1, C.UNSURE: 2, C.NOT_COLLEGE: 3, C.UNREADABLE: 4}


def render(plan: dict) -> str:
    items = sorted(plan["items"], key=lambda it: (_ORDER[it["status"]], (it["dest_dir"] or "").casefold(),
                                                  it["name"].casefold()))
    rows, last = [], None
    for it in items:
        label = _label(it)
        shown = label if label != last else ""
        last = label
        reason = it["reason"] or ""
        rows.append([shown, it["name"], it["doc_type"] or "", f"{it['confidence']:.2f}" if it["status"] not in (C.UNREADABLE,) else "",
                     reason])
    out = [table(["goes to", "file", "type", "conf", "why"], rows,
                 title=f"Plan {plan['id']}  ·  {plan['model']}  ·  target {plan['target_root']}")]

    counts = {s: sum(1 for it in plan["items"] if it["status"] == s) for s in _ORDER}
    new_subjects = sorted({it["new_subject"]["name"] for it in plan["items"] if it["status"] in (C.NEW, C.ASK)})
    out.append("")
    out.append(f"{counts[C.SORTED]} to sort · {counts[C.NEW] + counts[C.ASK]} in new subjects"
               + (f" ({', '.join(new_subjects)})" if new_subjects else "")
               + f" · {counts[C.UNSURE]} need a look · {counts[C.NOT_COLLEGE] + counts[C.UNREADABLE]} left alone")
    noisy = [s for s in plan["skipped"] if s["why"] != "hidden"]
    if noisy:
        out.append(c("skipped: " + ", ".join(f"{Path(s['path']).name} ({s['why']})" for s in noisy), "dim"))
    out.append(c("Nothing has moved. Run `sloth apply` to review and confirm.", "bold"))
    return "\n".join(out)
