"""Command-line interface."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import __version__, config
from . import apply as apply_mod
from . import classify as classify_mod
from . import coldstart
from . import plan as plan_mod
from .cache import Cache
from .registry import Registry, Subject, norm
from .units import unit_from_filename
from .extract import extract, extract_cached
from .ollama import NotLocalError, Ollama
from .ui import c, table

OK, MISSING, WARN = c("ok", "green"), c("missing", "red"), c("warn", "yellow")

# (command, version flag, what it's for, required?, install hint)
TOOLS = [
    ("pdftotext", "-v", "PDF text", True, "sudo pacman -S poppler"),
    ("pdftoppm", "-v", "render scanned PDF pages", True, "sudo pacman -S poppler"),
    ("tesseract", "--version", "OCR for scans and photos", True, "sudo pacman -S tesseract tesseract-data-eng"),
    ("soffice", "--version", "old .ppt/.doc files", False, "optional; .ppt/.doc will be skipped"),
    ("notify-send", "--version", "desktop notifications (watch mode)", False, "optional"),
]


def _tool_version(path: str, flag: str) -> str:
    try:
        out = subprocess.run([path, flag], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return path
    text = (out.stdout or out.stderr).strip()
    return text.splitlines()[0] if text else path


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check Ollama, models and helper tools, and show where config and state live."""
    cfg = config.load()
    rows: list[list[str]] = []
    healthy = True

    try:
        ol = Ollama(cfg.host, timeout=5)
        models = ol.models()
        rows.append(["ollama", OK, f"{ol.version()} at {cfg.host}"])
        gemmas = [m for m in models if m.startswith("gemma")]
        rows.append(["gemma models", OK if gemmas else MISSING, ", ".join(gemmas) or "none"])
        has_model = cfg.model in models
        rows.append(["configured model", OK if has_model else MISSING, cfg.model])
        healthy &= has_model
    except NotLocalError as e:
        rows.append(["ollama", MISSING, str(e)])
        healthy = False
    except OSError as e:  # connection refused, timeouts
        rows.append(["ollama", MISSING, f"not reachable at {cfg.host}: {e}"])
        healthy = False

    for cmd, flag, purpose, required, hint in TOOLS:
        path = shutil.which(cmd)
        if path:
            rows.append([cmd, OK, f"{_tool_version(path, flag)}  ({purpose})"])
        else:
            rows.append([cmd, MISSING if required else WARN, f"{hint}  ({purpose})"])
            healthy &= not required

    rows.append(["config", OK, str(config.config_path())])
    rows.append(["state", OK, str(cfg.state)])
    rows.append(["watched", OK, ", ".join(map(str, cfg.watched))])
    rows.append(["target root", OK, str(cfg.target_root)])
    rows.append([
        "real folders",
        WARN if cfg.allow_real_folders else OK,
        "ALLOWED (allow_real_folders = true)" if cfg.allow_real_folders else "locked (sandbox only)",
    ])

    print(table(["check", "status", "details"], rows, title=f"sloth {__version__} doctor"))
    return 0 if healthy else 1


def _expand_paths(paths: list[str]) -> list[Path]:
    """Files as given; directories expanded one level, hidden files skipped."""
    out = []
    for p in map(Path, paths):
        p = p.expanduser()
        if p.is_dir():
            out.extend(sorted((f for f in p.iterdir() if f.is_file() and not f.name.startswith(".")),
                              key=lambda f: f.name.casefold()))
        elif p.is_file():
            out.append(p)
        else:
            print(c(f"not found: {p}", "red"))
    return out


def cmd_extract(args: argparse.Namespace) -> int:
    """Show what sloth reads from each file (no model involved)."""
    cfg = config.load()
    for p in args.paths:  # before listing anything
        config.check_allowed(cfg, Path(p).expanduser())
    files = _expand_paths(args.paths)
    cache = Cache(cfg.cache_path)
    rows, total = [], 0.0
    for f in files:
        r = extract_cached(f, cfg, cache) if not args.no_cache else {
            **extract(f, max_pages=cfg.max_pages, max_slides=cfg.max_slides, max_chars=cfg.max_chars),
            "cached": False,
        }
        total += r["seconds"] if not r["cached"] else 0
        if r["error"]:
            snippet = c(r["error"], "red")
        else:
            snippet = r["text"][: args.snippet].replace("\n", " ⏎ ")
        rows.append([
            f.name,
            r["method"],
            str(r["pages_read"]),
            c("yes", "yellow") if r["is_scan"] else "no",
            str(r["chars"]),
            c("cached", "dim") if r["cached"] else f"{r['seconds']:.2f}s",
            snippet,
        ])
    cache.close()
    print(table(["file", "method", "pages", "scan", "chars", "time", "starts with"], rows))
    print(c(f"{len(files)} files, {total:.1f}s of extraction work", "dim"))
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Classify files and show where each would go. Moves nothing."""
    cfg = config.load()
    folders = [Path(f).expanduser() for f in args.folders] or cfg.watched
    for f in folders:
        config.check_allowed(cfg, f)
    registry = Registry.load(cfg.registry_path)
    if not registry.subjects:
        print(c("No subjects yet: everything will be proposed as new subjects. "
                "Run `sloth init` to propose some, or add them with `sloth subjects add`.", "yellow"))
    p = plan_mod.make_plan(folders, cfg, registry, recursive=args.recursive, fresh=args.fresh,
                           progress=plan_mod.progress_line)
    path = plan_mod.save_plan(p, cfg)
    print()
    print(plan_mod.render(p))
    print(c(f"plan saved to {path}", "dim"))
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    """Review the latest plan, confirm, and move the files."""
    return apply_mod.apply(config.load(), Path(args.plan) if args.plan else None)


def cmd_undo(args: argparse.Namespace) -> int:
    """Move the files of a batch back where they came from."""
    cfg = config.load()
    if args.list:
        return apply_mod.list_batches(cfg)
    return apply_mod.undo(cfg, args.batch, skip_changed=args.skip_changed)


def cmd_subjects(args: argparse.Namespace) -> int:
    """List and edit the subject registry."""
    cfg = config.load()
    reg = Registry.load(cfg.registry_path)
    action = args.action or "list"
    if action == "list":
        if not reg.subjects:
            print("No subjects yet. Add one with `sloth subjects add NAME`.")
            return 0
        rows = [[s.name, s.folder, ", ".join(s.aliases), ", ".join(s.keywords)] for s in reg.subjects]
        print(table(["subject", "folder", "aliases", "keywords"], rows))
        print(c(f"registry: {cfg.registry_path}", "dim"))
        return 0
    if action == "add":
        reg.add(Subject(args.name, aliases=args.alias or [], keywords=args.keyword or [],
                        description=args.description or "", folder=args.folder or ""))
        reg.save()
        print(f"Added {args.name}.")
        return 0

    subject = reg.get(args.name)
    if subject is None:
        print(c(f"No subject called {args.name!r}.", "red"))
        return 1
    if action == "alias":
        for a in args.aliases:
            if reg.get(a) and reg.get(a) is not subject:
                print(c(f"{a!r} already belongs to {reg.get(a).name}.", "red"))
                return 1
            if norm(a) not in map(norm, subject.names()):
                subject.aliases.append(a)
        reg.save()
        print(f"{subject.name}: aliases are now {', '.join(subject.aliases)}")
    elif action == "rename":
        if reg.get(args.new_name) and reg.get(args.new_name) is not subject:
            print(c(f"{args.new_name!r} already exists; use `sloth subjects merge`.", "red"))
            return 1
        old = subject.name
        subject.name = args.new_name
        if norm(old) not in map(norm, subject.names()):
            subject.aliases.append(old)  # so files and plans that say the old name still match
        if args.folder:
            subject.folder = args.folder
        reg.save()
        print(f"Renamed {old} → {subject.name}. Folder stays {subject.folder!r}"
              + ("" if args.folder else " (pass --folder to change where new files go)") + ".")
    elif action == "merge":
        into = reg.get(args.into)
        if into is None or into is subject:
            print(c(f"No other subject called {args.into!r}.", "red"))
            return 1
        for n in subject.names():
            if norm(n) not in map(norm, into.names()):
                into.aliases.append(n)
        into.keywords += [k for k in subject.keywords if k not in into.keywords]
        reg.subjects.remove(subject)
        reg.save()
        print(f"Merged {subject.name} into {into.name}. Files already in {subject.folder!r} were not moved.")
    return 0


def cmd_explain(args: argparse.Namespace) -> int:
    """Show what sloth read from a file and why it decided what it did."""
    cfg = config.load()
    path = Path(args.file).expanduser()
    config.check_allowed(cfg, path)
    if not path.is_file():
        print(c(f"not found: {path}", "red"))
        return 1
    registry = Registry.load(cfg.registry_path)
    cache = Cache(cfg.cache_path)
    try:
        ex = extract_cached(path, cfg, cache)
        got = classify_mod.classify(path, ex, registry, Ollama(cfg.host), cfg, cache=None if args.fresh else cache)
    finally:
        cache.close()

    print(c(path.name, "bold"))
    print(c(f"read: {ex['method']}, {ex['pages_read']} page(s), {ex['chars']} chars"
            + (", OCR" if ex["is_scan"] else "") + ("" if ex["cached"] else f", {ex['seconds']:.2f}s"), "dim"))
    print()
    print(c(f"── text sent to the model (first {args.chars} of {ex['chars']} chars) ──", "blue"))
    print(ex["text"][: args.chars] or c("(no text)", "dim"))
    print()
    raw = got.get("raw")
    if raw:
        print(c(f"── {cfg.model} said" + (" (cached)" if got["cached"] else f" in {got['model_s']:.1f}s") + " ──", "blue"))
        for k in ("is_college", "subject", "new_subject", "unit_evidence", "unit", "doc_type", "confidence", "reason"):
            print(f"  {k:<13} {json.dumps(raw.get(k), ensure_ascii=False)}")
        print()
    print(c("── sloth decided ──", "blue"))
    item = {**got, "name": path.name}
    where = plan_mod.dest_dir(item, registry)
    status = got["status"].replace("_", " ")
    print(f"  {status}" + (f"  →  {Path(cfg.target_root, where, path.name)}" if where else "  (stays where it is)"))
    if got.get("suggestion"):
        print(f"  did you mean {got['suggestion']}?")
    if got["unit"]:
        print(f"  unit {got['unit']} from the {got['unit_source']}")
    named = unit_from_filename(path.name)
    if named is not None and named != got["unit"]:
        print(f"  the filename says unit {named}, but the content wins")
    if got.get("unit_note"):
        print(f"  note: {got['unit_note']}")
    if got["status"] == classify_mod.UNSURE and got["confidence"] < cfg.confidence_threshold:
        print(f"  confidence {got['confidence']:.2f} is below the threshold {cfg.confidence_threshold}")
    if not raw:
        print(f"  {got['reason']}")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    """Propose a subject list from a sample of your files."""
    cfg = config.load()
    folders = [Path(f).expanduser() for f in args.folders] or cfg.watched
    for f in folders:
        config.check_allowed(cfg, f)
    registry = Registry.load(cfg.registry_path)
    candidates, total = coldstart.sample_files(folders, cfg, args.sample, args.recursive)
    print(c(f"{total} files found, {len(candidates)} of a type sloth reads; sampling up to {args.sample}.", "dim"))

    def progress(i, n, path):
        print(c(f"[{i:>2}/{n}] ", "dim") + path.name, file=sys.stderr, flush=True)

    snippets = coldstart.collect_snippets(candidates, cfg, args.sample, progress)
    if not snippets:
        print("No readable files to learn from.")
        return 1
    print(c(f"Asking {cfg.model} which courses these {len(snippets)} files belong to…", "dim"))
    try:
        proposals, took = coldstart.propose(snippets, registry, Ollama(cfg.host), cfg.model)
    except ValueError as e:
        print(c(str(e), "red"))
        return 1
    print(c(f"{len(proposals)} subject(s) proposed in {took:.0f}s.", "dim"))
    if not proposals:
        print("No course material found in the sample.")
        return 0

    staged = coldstart.review(proposals, snippets, registry)
    print()
    if not staged.subjects:
        print("No subjects kept. Nothing saved.")
        return 0
    print(coldstart.show_registry(staged))
    if not apply_mod.confirm(f"Save these {len(staged.subjects)} subject(s) to {cfg.registry_path}?"):
        print("Cancelled. Nothing saved.")
        return 1
    staged.save()
    print("Saved. Next: `sloth plan`.")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    """Open the local web UI."""
    from .web import serve

    serve(config.load(), port=args.port, open_browser=not args.no_browser)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sloth",
        description="sloth: sorts your college files so you don't have to. "
        "Dry run by default; nothing moves without `sloth apply`.",
    )
    parser.add_argument("--version", action="version", version=f"sloth {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")

    p = sub.add_parser("doctor", help="check Ollama, models and helper tools")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("extract", help="show the text sloth reads from files (no model)")
    p.add_argument("paths", nargs="+", help="files or folders (folders: top level only)")
    p.add_argument("--no-cache", action="store_true", help="re-extract even if cached")
    p.add_argument("--snippet", type=int, default=60, metavar="N", help="characters of text to show")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("init", help="propose a subject list from a sample of your files")
    p.add_argument("folders", nargs="*", help="folders to sample (default: watched folders)")
    p.add_argument("--sample", type=int, default=40, metavar="N", help="how many files to look at (default 40)")
    p.add_argument("-r", "--recursive", action="store_true", help="also look inside subfolders")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("plan", help="classify files and show where they'd go (moves nothing)")
    p.add_argument("folders", nargs="*", help="folders to scan (default: watched folders from the config)")
    p.add_argument("-r", "--recursive", action="store_true", help="also look inside subfolders")
    p.add_argument("--fresh", action="store_true", help="ask the model again instead of using cached answers")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("apply", help="review the latest plan, confirm, and move files")
    p.add_argument("--plan", help="a specific plan file instead of the latest")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("undo", help="move the last batch (or --batch ID) back")
    p.add_argument("--batch", help="batch id (see --list)")
    p.add_argument("--list", action="store_true", help="list batches")
    p.add_argument("--skip-changed", action="store_true", help="undo what can be undone, leave changed files")
    p.set_defaults(func=cmd_undo)

    p = sub.add_parser("subjects", help="list or edit subjects")
    p.set_defaults(func=cmd_subjects, action=None)
    ss = p.add_subparsers(dest="action", metavar="action")
    q = ss.add_parser("list", help="list subjects")
    q = ss.add_parser("add", help="add a subject")
    q.add_argument("name")
    q.add_argument("--alias", action="append", help="another name for it (repeatable)")
    q.add_argument("--keyword", action="append", help="a topic that identifies it (repeatable)")
    q.add_argument("--description")
    q.add_argument("--folder", help="folder name (default: the subject name)")
    q = ss.add_parser("alias", help="add aliases to a subject")
    q.add_argument("name")
    q.add_argument("aliases", nargs="+")
    q = ss.add_parser("rename", help="rename a subject (old name kept as an alias)")
    q.add_argument("name")
    q.add_argument("new_name")
    q.add_argument("--folder", help="also change the folder new files go to")
    q = ss.add_parser("merge", help="merge one subject into another")
    q.add_argument("name")
    q.add_argument("into")

    p = sub.add_parser("ui", help="open the local web UI (http://127.0.0.1 only)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    p.set_defaults(func=cmd_ui)

    p = sub.add_parser("explain", help="show what sloth read from a file and why it decided")
    p.add_argument("file")
    p.add_argument("--chars", type=int, default=600, help="how much of the text to show")
    p.add_argument("--fresh", action="store_true", help="ask the model again")
    p.set_defaults(func=cmd_explain)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except PermissionError as e:
        print(c(f"refused: {e}", "red"))
        return 2
    except KeyboardInterrupt:
        print(c("\ninterrupted", "yellow"))
        return 130
