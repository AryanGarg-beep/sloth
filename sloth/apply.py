"""Carry out a plan (after asking), and reverse it."""

from __future__ import annotations

import copy
import errno
import json
import os
import time
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


def _choose(prompt: str, options: str, default: str, ask) -> str:
    while True:
        try:
            answer = ask(prompt).strip().lower() or default
        except EOFError:
            return "s"
        if answer[:1] in options:
            return answer[:1]
        print(f"Please answer one of: {', '.join(options)}")


def _pick_subject(registry: Registry, default: str | None, ask) -> Subject | None:
    names = [s.name for s in registry.subjects]
    if not names:
        print("There are no subjects to merge into yet.")
        return None
    for i, n in enumerate(names, start=1):
        print(f"   {i}. {n}")
    d = str(names.index(default) + 1) if default in names else ""
    try:
        answer = ask(f"   merge into which? [{d}] ").strip() or d
    except EOFError:
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(names):
        return registry.subjects[int(answer) - 1]
    return registry.get(answer)


def resolve_new_subjects(items: list[dict], registry: Registry, ask=input) -> bool:
    """Ask about each proposed subject. Updates items in place and stages changes in
    `registry` (a copy; the caller saves it). Returns True if the registry changed."""
    groups: dict[str, list[dict]] = {}
    for it in items:
        if it["status"] in (C.NEW, C.ASK):
            groups.setdefault(norm(it["new_subject"]["name"]), []).append(it)
    changed = False
    for group in groups.values():
        first = group[0]
        proposed = first["new_subject"]
        files = ", ".join(it["name"] for it in group)
        print()
        print(c(f"New subject proposed: {proposed['name']}", "bold") + f"  ({len(group)} file(s): {files})")
        if proposed.get("keywords"):
            print(c(f"   keywords: {', '.join(proposed['keywords'])}", "dim"))
        suggestion = first.get("suggestion")
        # It may already exist by now (an earlier answer in this run created it).
        existing = registry.get(proposed["name"])
        if existing:
            target = existing
        else:
            if suggestion:
                print(f"   This looks like your existing subject {c(suggestion, 'bold')}.")
            default = "m" if suggestion else "a"
            choice = _choose(f"   [a]ccept as new · [r]ename · [m]erge into existing · [s]kip these files  [{default}] ",
                             "arms", default, ask)
            target = None
            if choice == "a":
                target = Subject(proposed["name"], keywords=proposed.get("keywords", []))
                registry.add(target)
                changed = True
            elif choice == "r":
                try:
                    name = ask("   new name: ").strip()
                except EOFError:
                    name = ""
                if name:
                    target = registry.get(name)
                    if target is None:
                        target = Subject(name, aliases=[proposed["name"]], keywords=proposed.get("keywords", []))
                        registry.add(target)
                    elif norm(proposed["name"]) not in map(norm, target.names()):
                        target.aliases.append(proposed["name"])
                    changed = True
            elif choice == "m":
                target = _pick_subject(registry, suggestion, ask)
                if target is not None and norm(proposed["name"]) not in map(norm, target.names()):
                    target.aliases.append(proposed["name"])  # so next time it's recognised directly
                    changed = True
        for it in group:
            if target is None:
                it["status"], it["dest_dir"] = SKIPPED, None
            else:
                it["status"], it["subject"] = C.SORTED, target.name
                it["dest_dir"] = dest_dir(it, registry)
    return changed


def _source_problem(it: dict) -> str | None:
    src = Path(it["src"])
    if not src.exists():
        return "no longer there"
    st = src.stat()
    if st.st_size != it["size"] or st.st_mtime != it["mtime"]:
        if sha256(src) != it["sha256"]:
            return "changed since the plan was made"
    return None


def apply(cfg: config.Config, plan_path: Path | None = None, ask=input) -> int:
    plan_path = plan_path or latest_plan_path(cfg)
    if plan_path is None or not plan_path.exists():
        print("No plan yet. Run `sloth plan` first.")
        return 1
    plan = load_plan(plan_path)
    if plan.get("applied_batch"):
        print(f"This plan was already applied (batch {plan['applied_batch']}). Run `sloth plan` again.")
        return 1
    target_root = Path(plan["target_root"])
    config.check_allowed(cfg, target_root)

    registry = Registry.load(cfg.registry_path)
    staged = copy.deepcopy(registry)
    items = plan["items"]
    for it in items:  # the registry may have changed since planning
        if it["status"] == C.SORTED:
            it["dest_dir"] = dest_dir(it, staged)
    registry_changed = resolve_new_subjects(items, staged, ask)

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

    print()
    if problems:
        for it, why in problems:
            print(c(f"skipping {it['name']}: {why}", "yellow"))
    if not moves:
        print("Nothing to move.")
        if registry_changed and confirm("Save the subject changes anyway?", ask):
            staged.save()
        return 0

    per_dest: dict[str, int] = {}
    for it in moves:
        per_dest[it["dest_dir"]] = per_dest.get(it["dest_dir"], 0) + 1
    rows = [[d, str(n)] for d, n in sorted(per_dest.items(), key=lambda kv: (kv[0] == NEEDS_LOOK, kv[0].casefold()))]
    print(table(["into " + str(target_root), "files"], rows))
    new = [s.name for s in staged.subjects if registry.get(s.name) is None]
    if new:
        print(f"New subjects: {', '.join(new)}")
    if not confirm(f"Move {len(moves)} file(s)?", ask):
        print("Cancelled. Nothing moved.")
        return 1

    if registry_changed:
        staged.save()
    journal = Journal(cfg.journal_path)
    batch = new_batch_id()
    journal.write({"type": "batch_start", "batch": batch, "plan": str(plan_path), "target_root": str(target_root),
                   "count": len(moves)})
    moved = failed = 0
    for it in moves:
        src = Path(it["src"])
        folder = target_root / it["dest_dir"]
        try:
            created = make_dirs(folder)
            journal.write({"type": "move", "state": "pending", "batch": batch, "src": str(src),
                           "dest_dir": str(folder), "sha256": it["sha256"], "size": it["size"], "created_dirs": created})
            final = safe_move(src, folder)
            journal.write({"type": "move", "state": "done", "batch": batch, "src": str(src), "dst": str(final),
                           "sha256": it["sha256"], "size": it["size"], "created_dirs": created})
            renamed = c(f"  (as {final.name})", "yellow") if final.name != src.name else ""
            print(c("moved ", "green") + f"{src.name} → {final.parent.relative_to(target_root)}{renamed}")
            moved += 1
        except OSError as e:
            journal.write({"type": "move", "state": "failed", "batch": batch, "src": str(src), "error": str(e)})
            print(c(f"failed {src.name}: {e}", "red"))
            failed += 1
    journal.write({"type": "batch_end", "batch": batch, "moved": moved, "failed": failed})

    plan["applied_batch"] = batch
    plan_path.write_text(json.dumps(plan, indent=1, ensure_ascii=False))
    print()
    print(c(f"Moved {moved} file(s)", "bold") + (f", {failed} failed" if failed else "")
          + f". Batch {batch}. Undo with `sloth undo`.")
    return 0 if not failed else 1


# --- undo ------------------------------------------------------------------------

def list_batches(cfg: config.Config) -> int:
    batches = Journal(cfg.journal_path).batches()
    rows = []
    for bid, b in batches.items():
        if not b["start"]:
            continue
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(b["start"]["ts"]))
        state = "undone" if b["undone"] else (f"{len(b['undone_files'])} undone" if b["undone_files"] else "")
        rows.append([bid, when, str(len(b["moves"])), state])
    print(table(["batch", "when", "moved", "state"], rows) if rows else "No batches yet.")
    return 0


def undo(cfg: config.Config, batch_id: str | None = None, skip_changed: bool = False, ask=input) -> int:
    journal = Journal(cfg.journal_path)
    batches = journal.batches()
    if batch_id is None:
        candidates = [bid for bid, b in batches.items()
                      if b["start"] and b["moves"] and not b["undone"]]
        if not candidates:
            print("Nothing to undo.")
            return 1
        batch_id = max(candidates, key=lambda bid: batches[bid]["start"]["ts"])
    b = batches.get(batch_id)
    if not b or not b["start"]:
        print(f"No batch {batch_id}. See `sloth undo --list`.")
        return 1
    if b["undone"]:
        print(f"Batch {batch_id} was already undone.")
        return 1

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
    for p in b["pending"].values():
        print(c(f"warning: a move of {p['src']} was started but never confirmed; check it by hand", "yellow"))

    for m, why in problems:
        print(c(f"{Path(m['dst']).name} {why}", "red"))
    if problems and not skip_changed:
        print("Refusing to undo, so nothing was moved. Use --skip-changed to undo the other files.")
        return 1
    if not todo:
        print("Nothing left to undo in this batch.")
        return 1
    if not confirm(f"Undo batch {batch_id}: move {len(todo)} file(s) back where they came from?", ask):
        print("Cancelled. Nothing moved.")
        return 1

    restored = failed = 0
    journal.write({"type": "undo_start", "batch": batch_id})
    for m in todo:
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
            print(c("restored ", "green") + str(final))
            restored += 1
        except OSError as e:
            journal.write({"type": "undo", "state": "failed", "batch": batch_id, "dst": str(dst), "error": str(e)})
            print(c(f"failed {dst.name}: {e}", "red"))
            failed += 1

    # Remove folders this batch created, if they're empty now. rmdir never deletes files.
    created = {d for m in b["moves"] for d in m.get("created_dirs", [])}
    for d in sorted(created, key=lambda p: p.count("/"), reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass

    complete = not failed and not problems
    journal.write({"type": "undo_end", "batch": batch_id, "restored": restored, "failed": failed, "complete": complete})
    print()
    print(c(f"Restored {restored} file(s)", "bold") + (f", {failed} failed" if failed else "") + ".")
    return 0 if complete else 1
