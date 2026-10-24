"""What a UI needs from sloth: the plan as data, corrections, apply, undo, explain.

Used by the web UI (`sloth ui`). No printing and no prompting here; errors are
raised as ValueError with a message meant for the user.
"""

from __future__ import annotations

import json
import os
import threading
import traceback
from pathlib import Path

from . import __version__, config
from . import apply as A
from .cache import Cache
from .extract import cached_extraction
from .ollama import Ollama
from .plan import latest_plan_path, load_plan, make_plan, save_plan
from .registry import Registry, Subject, norm
from .units import unit_from_filename


SHORTCUTS = [config.HOME / n for n in ("Downloads", "Documents", "Desktop")]


class Service:
    def __init__(self, cfg: config.Config):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.job = {"running": False, "done": 0, "total": 0, "current": None, "error": None}

    # --- reading -----------------------------------------------------------------

    def _plan(self) -> tuple[dict | None, Path | None]:
        path = latest_plan_path(self.cfg)
        if path is None or not path.exists():
            return None, None
        return load_plan(path), path

    def _ollama_status(self) -> dict:
        try:
            models = Ollama(self.cfg.host, timeout=2).models()
            return {"ok": True, "has_model": self.cfg.model in models}
        except Exception as e:  # noqa: BLE001 - anything means "not reachable"
            return {"ok": False, "error": str(e)}

    def state(self) -> dict:
        registry = Registry.load(self.cfg.registry_path)
        plan, path = self._plan()
        out = {
            "version": __version__,
            "model": self.cfg.model,
            "ollama": self._ollama_status(),
            "watched": [str(p) for p in self.cfg.watched],
            "target_root": str(self.cfg.target_root),
            "home": str(config.HOME),
            "shortcuts": [str(p) for p in SHORTCUTS if p.is_dir()],
            "threshold": self.cfg.confidence_threshold,
            "subjects": [{"name": s.name, "folder": s.folder, "aliases": s.aliases, "keywords": s.keywords}
                         for s in registry.subjects],
            "job": dict(self.job),
            "batches": A.batches_summary(self.cfg)[:10],
            "plan": None,
        }
        if plan:
            decisions = plan.get("subject_decisions", {})
            prep = A.prepare(self.cfg, plan, A.stored_decider(decisions))
            # Once applied, the sources are gone on purpose: that's not a problem to show.
            problems = {} if plan.get("applied_batch") else {it["src"]: why for it, why in prep.problems}
            items = []
            for i, (orig, eff) in enumerate(zip(plan["items"], prep.items)):
                items.append({
                    "index": i, "name": orig["name"], "doc_type": orig["doc_type"], "confidence": orig["confidence"],
                    "reason": orig["reason"], "status": orig["status"], "subject": orig.get("subject"),
                    "unit": orig.get("unit"), "suggestion": orig.get("suggestion"),
                    "new_subject": orig.get("new_subject"), "override": orig.get("override"),
                    "effective_status": eff["status"], "dest_dir": eff.get("dest_dir"),
                    "problem": problems.get(orig["src"]), "unit_note": orig.get("unit_note"),
                    "proposal_key": norm(orig["new_subject"]["name"]) if orig.get("new_subject") else None,
                })
            proposals = []
            for key, group in A.proposal_groups([dict(it) for it in plan["items"]]).items():
                first = group[0]
                proposals.append({"key": key, "name": first["new_subject"]["name"],
                                  "keywords": first["new_subject"].get("keywords", []),
                                  "suggestion": first.get("suggestion"),
                                  "files": [it["name"] for it in group], "decision": decisions.get(key)})
            out["plan"] = {
                "id": plan["id"], "created": plan["created"], "folders": plan["folders"],
                "target_root": plan["target_root"],
                "applied_batch": plan.get("applied_batch"), "items": items, "proposals": proposals,
                "moves": len(prep.moves), "per_destination": prep.per_destination(),
                "new_subjects": prep.new_subjects,
                "skipped": [s for s in plan.get("skipped", []) if s["why"] != "hidden"],
            }
        return out

    def explain(self, index: int) -> dict:
        plan, _ = self._plan()
        if not plan or not 0 <= index < len(plan["items"]):
            raise ValueError("no such file in the plan")
        it = plan["items"][index]
        cache = Cache(self.cfg.cache_path)
        try:
            ex = cached_extraction(it["sha256"], self.cfg, cache)
        finally:
            cache.close()
        return {"name": it["name"], "src": it["src"], "extract_method": it["extract_method"],
                "text": (ex or {}).get("text"), "raw": it.get("raw"), "status": it["status"],
                "unit": it.get("unit"), "unit_source": it.get("unit_source"), "unit_note": it.get("unit_note"),
                "reason": it["reason"], "confidence": it["confidence"], "model": plan["model"],
                "filename_unit": unit_from_filename(it["name"])}

    def browse(self, path: str | None) -> dict:
        """The subfolders of one folder, for the folder picker. Names only, hidden ones left out."""
        p = (Path(path).expanduser() if path else config.HOME).resolve()
        if not p.is_dir():
            raise ValueError(f"{p} is not a folder")
        try:
            dirs = sorted((e.name for e in os.scandir(p) if not e.name.startswith(".") and e.is_dir()), key=str.lower)
        except PermissionError:
            raise ValueError(f"can't open {p}") from None
        return {"path": str(p), "parent": str(p.parent) if p != p.parent else None, "dirs": dirs}

    # --- changing ----------------------------------------------------------------

    def set_folders(self, watched: list[str], target: str) -> None:
        """Choose which folders to sort and where sorted files go; saved to the config file."""
        folders: list[Path] = []
        for w in watched or []:
            p = Path(w).expanduser().resolve()
            if not p.is_dir():
                raise ValueError(f"{p} is not a folder")
            if p not in folders:
                folders.append(p)
        if not folders:
            raise ValueError("pick at least one folder to sort")
        t = Path(str(target or "")).expanduser().resolve()
        if not str(target or "").strip() or (t.exists() and not t.is_dir()):
            raise ValueError("pick a folder for the sorted files")
        if not t.exists() and not t.parent.is_dir():
            raise ValueError(f"{t.parent} doesn't exist")
        for p in folders + [t]:
            config.check_allowed(self.cfg, p)
        for p in folders:
            if p == t or t in p.parents:
                raise ValueError(f"{p} is inside the sorted folder {t}; sloth would re-sort its own work")
        with self.lock:
            if self.job["running"]:
                raise ValueError("wait for the plan to finish first")
            config.save_folders(folders, t)
            self.cfg.watched, self.cfg.target_root = folders, t

    def start_plan(self, recursive: bool) -> None:
        with self.lock:
            if self.job["running"]:
                raise ValueError("a plan is already being made")
            self.job.update(running=True, done=0, total=0, current=None, error=None)

        def progress(i, n, item):
            self.job.update(done=i, total=n, current=item["name"])

        def run():
            try:
                registry = Registry.load(self.cfg.registry_path)
                plan = make_plan(self.cfg.watched, self.cfg, registry, recursive=recursive, progress=progress)
                save_plan(plan, self.cfg)
            except Exception as e:  # noqa: BLE001 - reported to the page
                self.job["error"] = str(e)
                traceback.print_exc()
            finally:
                self.job["running"] = False

        threading.Thread(target=run, daemon=True).start()

    def _edit_plan(self, fn) -> None:
        with self.lock:
            plan, path = self._plan()
            if not plan:
                raise ValueError("no plan yet")
            if plan.get("applied_batch"):
                raise ValueError("this plan was already applied; make a new one")
            fn(plan)
            path.write_text(json.dumps(plan, indent=1, ensure_ascii=False))

    def set_override(self, index: int, override: dict | None) -> None:
        if override is not None:
            action = override.get("action")
            if action not in ("skip", "needs_look", "subject"):
                raise ValueError("unknown correction")
            if action == "subject":
                if not Registry.load(self.cfg.registry_path).get(override.get("subject")):
                    raise ValueError("unknown subject")
                unit = override.get("unit")
                override = {"action": "subject", "subject": override["subject"],
                            "unit": int(unit) if unit not in (None, "") else None}
            else:
                override = {"action": action}

        def fn(plan):
            plan["items"][index]["override"] = override
            if override is None:
                plan["items"][index].pop("override")
        self._edit_plan(fn)

    def set_decision(self, key: str, decision: dict | None) -> None:
        if decision is not None and decision.get("action") not in ("accept", "rename", "merge", "skip"):
            raise ValueError("unknown decision")

        def fn(plan):
            decisions = plan.setdefault("subject_decisions", {})
            if decision is None:
                decisions.pop(key, None)
            else:
                decisions[key] = {k: v for k, v in decision.items() if k in ("action", "name", "into")}
        self._edit_plan(fn)

    def apply(self) -> dict:
        with self.lock:
            plan, path = self._plan()
            if not plan:
                raise ValueError("no plan yet")
            if plan.get("applied_batch"):
                raise ValueError("this plan was already applied")
            prep = A.prepare(self.cfg, plan, A.stored_decider(plan.get("subject_decisions", {})))
            if not prep.moves:
                raise ValueError("nothing to move")
            return A.execute(self.cfg, plan, path, prep, log=lambda *_: None)

    def undo_preview(self, batch: str | None) -> dict:
        pv = A.undo_preview(self.cfg, batch)
        return {"batch": pv["batch"], "error": pv["error"], "count": len(pv["todo"]),
                "problems": [{"file": Path(m["dst"]).name, "why": why} for m, why in pv["problems"]],
                "pending": [p["src"] for p in pv["pending"]]}

    def undo(self, batch: str | None, skip_changed: bool) -> dict:
        with self.lock:
            pv = A.undo_preview(self.cfg, batch)
            if pv["error"]:
                raise ValueError(pv["error"])
            if pv["problems"] and not skip_changed:
                raise ValueError("some files changed since they were moved; nothing was undone")
            return A.undo_execute(self.cfg, pv, log=lambda *_: None)

    def add_subject(self, data: dict) -> None:
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("a subject needs a name")
        split = lambda s: [x.strip() for x in str(s or "").split(",") if x.strip()]  # noqa: E731
        with self.lock:
            reg = Registry.load(self.cfg.registry_path)
            reg.add(Subject(name, aliases=split(data.get("aliases")), keywords=split(data.get("keywords"))))
            reg.save()
