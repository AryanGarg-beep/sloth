"""Carry out a plan (after asking), and reverse it.

The work is split so the CLI and the web UI share it:
  prepare()  -> what would happen (subject decisions, per-file corrections, source checks)
  execute()  -> do the moves and journal them
  undo_preview() / undo_execute() -> the same for undo
The CLI wrappers apply() and undo() add the terminal prompts.
"""

from __future__ import annotations

import copy
import errno
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import classify as C
from . import config
from .cache import sha256
from .moves import Journal, make_dirs, new_batch_id, rename_noreplace, safe_move
from .plan import NEEDS_LOOK, dest_dir, latest_plan_path, load_plan
from .registry import Registry, Subject, norm
from .ui import c, table

SKIPPED = "skipped"


def confirm(question: str, ask=input) -> bool:
    try:
        return ask(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


# --- per-file corrections ------------------------------------------------------------

def apply_override(item: dict, registry: Registry) -> None:
    """Apply a correction stored on a plan item (from the web UI) before moving.

    override = {"action": "skip"} | {"action": "needs_look"} | {"action": "subject", "subject": .., "unit": ..}
    """
    ov = item.get("override")
    if ov:
        action = ov.get("action")
        if action == "skip":
            item["status"] = SKIPPED
        elif action == "needs_look":
            item["status"] = C.UNSURE
        elif action == "subject":
            item["status"], item["subject"], item["unit"] = C.SORTED, ov["subject"], ov.get("unit")
    item["dest_dir"] = dest_dir(item, registry)


# --- new subject decisions -------------------------------------------------------------

def proposal_groups(items: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for it in items:
        if it["status"] in (C.NEW, C.ASK):
            groups.setdefault(norm(it["new_subject"]["name"]), []).append(it)
    return groups


def apply_subject_decision(group: list[dict], decision: dict, staged: Registry) -> bool:
    """decision = {"action": "accept" | "rename" | "merge" | "skip", "name": .., "into": ..}.
    Updates the group's items and the staged registry. Returns True if the registry changed."""
    proposed = group[0]["new_subject"]
    action = decision.get("action", "skip")
    target, changed = staged.get(proposed["name"]), False
    if target is None:
        if action == "accept":
            target = Subject(proposed["name"], keywords=proposed.get("keywords", []))
            staged.add(target)
            changed = True
        elif action == "rename" and decision.get("name", "").strip():
            name = decision["name"].strip()
            target = staged.get(name)
            if target is None:
                target = Subject(name, aliases=[proposed["name"]], keywords=proposed.get("keywords", []))
                staged.add(target)
            elif norm(proposed["name"]) not in map(norm, target.names()):
                target.aliases.append(proposed["name"])
            changed = True
        elif action == "merge":
            target = staged.get(decision.get("into"))
            if target is not None and norm(proposed["name"]) not in map(norm, target.names()):
                target.aliases.append(proposed["name"])  # so next time it's recognised directly
                changed = True
    for it in group:
        if target is None:
            it["status"], it["dest_dir"] = SKIPPED, None
        else:
            it["status"], it["subject"] = C.SORTED, target.name
            it["dest_dir"] = dest_dir(it, staged)
    return changed


def interactive_decider(ask=input):
    """A decide() callback that asks in the terminal."""

    def choose(prompt, options, default):
        while True:
            try:
                answer = ask(prompt).strip().lower() or default
            except EOFError:
                return "s"
            if answer[:1] in options:
                return answer[:1]
            print(f"Please answer one of: {', '.join(options)}")

    def decide(group: list[dict], staged: Registry) -> dict:
        first, proposed = group[0], group[0]["new_subject"]
        files = ", ".join(it["name"] for it in group)
        print()
        print(c(f"New subject proposed: {proposed['name']}", "bold") + f"  ({len(group)} file(s): {files})")
        if proposed.get("keywords"):
            print(c(f"   keywords: {', '.join(proposed['keywords'])}", "dim"))
        if staged.get(proposed["name"]):
            return {"action": "accept"}  # created by an earlier answer in this run
        suggestion = first.get("suggestion")
        if suggestion:
            print(f"   This looks like your existing subject {c(suggestion, 'bold')}.")
        default = "m" if suggestion else "a"
        choice = choose(f"   [a]ccept as new · [r]ename · [m]erge into existing · [s]kip these files  [{default}] ",
                        "arms", default)
        if choice == "a":
            return {"action": "accept"}
        if choice == "r":
            try:
                return {"action": "rename", "name": ask("   new name: ")}
            except EOFError:
                return {"action": "skip"}
        if choice == "m":
            names = [s.name for s in staged.subjects]
            if not names:
                print("There are no subjects to merge into yet.")
                return {"action": "skip"}
            for i, n in enumerate(names, start=1):
                print(f"   {i}. {n}")
            d = str(names.index(suggestion) + 1) if suggestion in names else ""
            try:
                answer = ask(f"   merge into which? [{d}] ").strip() or d
            except EOFError:
                return {"action": "skip"}
            into = names[int(answer) - 1] if answer.isdigit() and 1 <= int(answer) <= len(names) else answer
            return {"action": "merge", "into": into}
        return {"action": "skip"}

    return decide


def stored_decider(decisions: dict):
    """A decide() callback that reads decisions saved in the plan (web UI). Undecided = skip."""
    return lambda group, staged: decisions.get(norm(group[0]["new_subject"]["name"]), {"action": "skip"})


# --- prepare / execute ----------------------------------------------------------------

@dataclass
class Prepared:
    items: list[dict]
    moves: list[dict]
    problems: list[tuple[dict, str]]
    registry: Registry
    staged: Registry
    registry_changed: bool
    target_root: Path
    new_subjects: list[str] = field(default_factory=list)

    def per_destination(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for it in self.moves:
            counts[it["dest_dir"]] = counts.get(it["dest_dir"], 0) + 1
        return sorted(counts.items(), key=lambda kv: (kv[0] == NEEDS_LOOK, kv[0].casefold()))


def _source_problem(it: dict) -> str | None:
    src = Path(it["src"])
    if not src.exists():
        return "no longer there"
    st = src.stat()
    if st.st_size != it["size"] or st.st_mtime != it["mtime"]:
        if sha256(src) != it["sha256"]:
            return "changed since the plan was made"
    return None


def prepare(cfg: config.Config, plan: dict, decide) -> Prepared:
    target_root = Path(plan["target_root"])
    config.check_allowed(cfg, target_root)
    registry = Registry.load(cfg.registry_path)
    staged = copy.deepcopy(registry)
    items = copy.deepcopy(plan["items"])
    for it in items:  # corrections, and the registry may have changed since planning
        apply_override(it, staged)
    changed = False
    for group in proposal_groups(items).values():
        changed |= apply_subject_decision(group, decide(group, staged), staged)

    moves, problems = [], []
    for it in items:
        if not it.get("dest_dir"):
            continue
        config.check_allowed(cfg, Path(it["src"]))
        why = _source_problem(it)
        if why:
            problems.append((it, why))
        else:
            moves.append(it)
    new = [s.name for s in staged.subjects if registry.get(s.name) is None]
    return Prepared(items, moves, problems, registry, staged, changed, target_root, new)


def execute(cfg: config.Config, plan: dict, plan_path: Path, prep: Prepared, log=print) -> dict:
    if prep.registry_changed:
        prep.staged.save()
    journal = Journal(cfg.journal_path)
    batch = new_batch_id()
    journal.write({"type": "batch_start", "batch": batch, "plan": str(plan_path),
                   "target_root": str(prep.target_root), "count": len(prep.moves)})
    moved, failed, results = 0, 0, []
    for it in prep.moves:
        src = Path(it["src"])
        folder = prep.target_root / it["dest_dir"]
        try:
            created = make_dirs(folder)
            journal.write({"type": "move", "state": "pending", "batch": batch, "src": str(src),
                           "dest_dir": str(folder), "sha256": it["sha256"], "size": it["size"], "created_dirs": created})
            final = safe_move(src, folder)
            journal.write({"type": "move", "state": "done", "batch": batch, "src": str(src), "dst": str(final),
                           "sha256": it["sha256"], "size": it["size"], "created_dirs": created})
            results.append({"file": src.name, "to": str(final.parent.relative_to(prep.target_root)),
                            "as": final.name if final.name != src.name else None, "ok": True})
            renamed = c(f"  (as {final.name})", "yellow") if final.name != src.name else ""
            log(c("moved ", "green") + f"{src.name} → {final.parent.relative_to(prep.target_root)}{renamed}")
            moved += 1
        except OSError as e:
            journal.write({"type": "move", "state": "failed", "batch": batch, "src": str(src), "error": str(e)})
            results.append({"file": src.name, "error": str(e), "ok": False})
            log(c(f"failed {src.name}: {e}", "red"))
            failed += 1
    journal.write({"type": "batch_end", "batch": batch, "moved": moved, "failed": failed})
    plan["applied_batch"] = batch
    plan_path.write_text(json.dumps(plan, indent=1, ensure_ascii=False))
    return {"batch": batch, "moved": moved, "failed": failed, "results": results}


def apply(cfg: config.Config, plan_path: Path | None = None, ask=input) -> int:
    """`sloth apply`: ask about new subjects, show a summary, confirm, move."""
    plan_path = plan_path or latest_plan_path(cfg)
    if plan_path is None or not plan_path.exists():
        print("No plan yet. Run `sloth plan` first.")
        return 1
    plan = load_plan(plan_path)
    if plan.get("applied_batch"):
        print(f"This plan was already applied (batch {plan['applied_batch']}). Run `sloth plan` again.")
        return 1
    prep = prepare(cfg, plan, interactive_decider(ask))

    print()
    for it, why in prep.problems:
        print(c(f"skipping {it['name']}: {why}", "yellow"))
    if not prep.moves:
        print("Nothing to move.")
        if prep.registry_changed and confirm("Save the subject changes anyway?", ask):
            prep.staged.save()
        return 0
    print(table(["into " + str(prep.target_root), "files"], [[d, str(n)] for d, n in prep.per_destination()]))
    if prep.new_subjects:
        print(f"New subjects: {', '.join(prep.new_subjects)}")
    if not confirm(f"Move {len(prep.moves)} file(s)?", ask):
        print("Cancelled. Nothing moved.")
        return 1
    result = execute(cfg, plan, plan_path, prep)
    print()
    print(c(f"Moved {result['moved']} file(s)", "bold") + (f", {result['failed']} failed" if result["failed"] else "")
          + f". Batch {result['batch']}. Undo with `sloth undo`.")
    return 0 if not result["failed"] else 1


# --- undo ------------------------------------------------------------------------------

def batches_summary(cfg: config.Config) -> list[dict]:
    out = []
    for bid, b in Journal(cfg.journal_path).batches().items():
        if not b["start"]:
            continue
        out.append({"batch": bid, "ts": b["start"]["ts"], "moved": len(b["moves"]), "undone": b["undone"],
                    "undone_files": len(b["undone_files"])})
    return sorted(out, key=lambda b: b["ts"], reverse=True)


def list_batches(cfg: config.Config) -> int:
    rows = []
    for b in batches_summary(cfg):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(b["ts"]))
        state = "undone" if b["undone"] else (f"{b['undone_files']} undone" if b["undone_files"] else "")
        rows.append([b["batch"], when, str(b["moved"]), state])
    print(table(["batch", "when", "moved", "state"], rows) if rows else "No batches yet.")
    return 0


def undo_preview(cfg: config.Config, batch_id: str | None = None) -> dict:
    """What undoing a batch would do. Moves nothing.

    Returns {"batch", "todo": [move records], "problems": [(record, why)], "pending": [records], "error"}.
    """
    batches = Journal(cfg.journal_path).batches()
    if batch_id is None:
        candidates = [bid for bid, b in batches.items() if b["start"] and b["moves"] and not b["undone"]]
        if not candidates:
            return {"batch": None, "todo": [], "problems": [], "pending": [], "error": "Nothing to undo."}
        batch_id = max(candidates, key=lambda bid: batches[bid]["start"]["ts"])
    b = batches.get(batch_id)
    if not b or not b["start"]:
        return {"batch": batch_id, "todo": [], "problems": [], "pending": [], "error": f"No batch {batch_id}."}
    if b["undone"]:
        return {"batch": batch_id, "todo": [], "problems": [], "pending": [],
                "error": f"Batch {batch_id} was already undone."}
    todo, problems = [], []
    for m in reversed(b["moves"]):
        if m["dst"] in b["undone_files"]:
            continue
        dst, src = Path(m["dst"]), Path(m["src"])
        if not dst.exists():
            problems.append((m, "is no longer where sloth put it"))
        elif sha256(dst) != m["sha256"]:
            problems.append((m, "has changed since it was moved"))
        elif src.exists() or src.is_symlink():
            problems.append((m, f"can't go back: something else is now at {src}"))
        else:
            todo.append(m)
    return {"batch": batch_id, "todo": todo, "problems": problems, "pending": list(b["pending"].values()),
            "created_dirs": sorted({d for m in b["moves"] for d in m.get("created_dirs", [])}), "error": None}


def undo_execute(cfg: config.Config, preview: dict, log=print) -> dict:
    journal = Journal(cfg.journal_path)
    batch_id = preview["batch"]
    restored, failed, results = 0, 0, []
    journal.write({"type": "undo_start", "batch": batch_id})
    for m in preview["todo"]:
        dst, src = Path(m["dst"]), Path(m["src"])
        try:
            make_dirs(src.parent)
            try:
                rename_noreplace(dst, src)
                final = src
            except OSError as e:
                if e.errno != errno.EXDEV:
                    raise
                final = safe_move(dst, src.parent, src.name)
            journal.write({"type": "undo", "state": "done", "batch": batch_id, "dst": str(dst), "restored": str(final)})
            results.append({"file": src.name, "to": str(final), "ok": True})
            log(c("restored ", "green") + str(final))
            restored += 1
        except OSError as e:
            journal.write({"type": "undo", "state": "failed", "batch": batch_id, "dst": str(dst), "error": str(e)})
            results.append({"file": dst.name, "error": str(e), "ok": False})
            log(c(f"failed {dst.name}: {e}", "red"))
            failed += 1

    # Remove folders this batch created, if they're empty now. rmdir never deletes files.
    for d in sorted(preview.get("created_dirs", []), key=lambda p: p.count("/"), reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass
    complete = not failed and not preview["problems"]
    journal.write({"type": "undo_end", "batch": batch_id, "restored": restored, "failed": failed, "complete": complete})
    return {"batch": batch_id, "restored": restored, "failed": failed, "complete": complete, "results": results}


def undo(cfg: config.Config, batch_id: str | None = None, skip_changed: bool = False, ask=input) -> int:
    """`sloth undo`: check everything first, confirm, move back."""
    pv = undo_preview(cfg, batch_id)
    if pv["error"]:
        print(pv["error"] + (" See `sloth undo --list`." if pv["error"].startswith("No batch") else ""))
        return 1
    for p in pv["pending"]:
        print(c(f"warning: a move of {p['src']} was started but never confirmed; check it by hand", "yellow"))
    for m, why in pv["problems"]:
        print(c(f"{Path(m['dst']).name} {why}", "red"))
    if pv["problems"] and not skip_changed:
        print("Refusing to undo, so nothing was moved. Use --skip-changed to undo the other files.")
        return 1
    if not pv["todo"]:
        print("Nothing left to undo in this batch.")
        return 1
    if not confirm(f"Undo batch {pv['batch']}: move {len(pv['todo'])} file(s) back where they came from?", ask):
        print("Cancelled. Nothing moved.")
        return 1
    result = undo_execute(cfg, pv)
    print()
    print(c(f"Restored {result['restored']} file(s)", "bold")
          + (f", {result['failed']} failed" if result["failed"] else "") + ".")
    return 0 if result["complete"] else 1
