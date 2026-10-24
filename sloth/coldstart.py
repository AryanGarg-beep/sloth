"""`sloth init`: look at a sample of files and propose a subject list.

One model call sees short snippets of up to ~40 files at once and proposes the
courses they belong to. You review the proposals; nothing is saved until you
confirm, and no files are moved.
"""

from __future__ import annotations

import copy
import json
import random
import time
from pathlib import Path

from . import config
from .cache import Cache
from .extract import SUPPORTED, extract_cached
from .registry import SIMILAR, Registry, Subject, norm
from .scan import scan
from .ui import c, table

SNIPPET_CHARS = 400

SCHEMA = {
    "type": "object",
    "properties": {
        "subjects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "description": {"type": "string"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                    "files": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["name", "aliases", "description", "keywords", "files"],
            },
        }
    },
    "required": ["subjects"],
}

SYSTEM = """You help a university student organise their files by course.
You get numbered files from their Downloads and Documents folders: each with its filename \
and the first few hundred characters of its text (OCR text may contain errors).
Some files are course material; others (receipts, tickets, memes, personal files) are not.

List the COURSES (subjects) that the course material belongs to:
- One entry per course. A course spans many topics: do not split one course into its topics \
or units (for example DFAs, regular expressions and Turing machines all belong to one automata course).
- Ignore files that are not course material.
- name: the course's usual full name, e.g. "Formal Languages and Automata".
- aliases: abbreviations and other names for the course that appear in the filenames or text, \
e.g. "FLA", "TOC", "DM". Only names that refer to this course.
- description: one short line.
- keywords: 5 to 10 distinctive topics that identify the course's material.
- files: the numbers of the files that belong to this course."""


def sample_files(folders: list[Path], cfg: config.Config, n: int, recursive: bool) -> tuple[list[Path], int]:
    """Up to n readable-looking files, picked evenly at random (seeded, so repeatable)."""
    files, _ = scan(folders, cfg, recursive=recursive)
    candidates = [f for f in files if f.suffix.lower() in SUPPORTED]
    random.Random(0).shuffle(candidates)
    return candidates, len(files)


def collect_snippets(candidates: list[Path], cfg: config.Config, n: int, progress=None) -> list[dict]:
    cache = Cache(cfg.cache_path)
    out = []
    try:
        for path in candidates:
            if len(out) >= n:
                break
            ex = extract_cached(path, cfg, cache)
            if ex["error"] or not ex["text"].strip():
                continue
            out.append({"path": path, "name": path.name, "text": " ".join(ex["text"].split())[:SNIPPET_CHARS]})
            if progress:
                progress(len(out), n, path)
    finally:
        cache.close()
    return out


def build_messages(snippets: list[dict], registry: Registry) -> list[dict]:
    lines = [f"[{i}] {s['name']}\n{s['text']}" for i, s in enumerate(snippets, start=1)]
    user = "Files:\n\n" + "\n\n".join(lines)
    if registry.subjects:
        user += ("\n\nThe student already has these subjects; reuse these exact names when a course matches: "
                 + ", ".join(s.name for s in registry.subjects))
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def propose(snippets: list[dict], registry: Registry, ollama, model: str) -> tuple[list[dict], float]:
    """Ask the model for subjects. Returns (proposals, seconds). Retries once on a bad reply."""
    messages = build_messages(snippets, registry)
    start = time.perf_counter()
    for _ in range(2):
        content, _ = ollama.chat(model, messages, schema=SCHEMA, options={"num_ctx": 16384})
        try:
            data = json.loads(content)
            return clean_proposals(data["subjects"], len(snippets)), time.perf_counter() - start
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    raise ValueError("the model didn't return a usable subject list; try again or add subjects by hand")


def clean_proposals(raw: list[dict], n_files: int) -> list[dict]:
    """Drop empty or duplicate proposals and out-of-range file numbers; biggest first."""
    out: dict[str, dict] = {}
    for p in raw:
        name = str(p.get("name", "")).strip()
        if not name:
            continue
        files = sorted({int(i) for i in p.get("files", []) if isinstance(i, int) and 1 <= i <= n_files})
        if not files:
            continue  # a subject with no evidence in the sample is a guess
        key = norm(name)
        aliases = [a.strip() for a in p.get("aliases", []) if str(a).strip() and norm(str(a)) != key]
        entry = out.setdefault(key, {"name": name, "aliases": [], "description": str(p.get("description", "")).strip(),
                                     "keywords": [], "files": []})
        entry["aliases"] += [a for a in aliases if norm(a) not in map(norm, entry["aliases"])]
        entry["keywords"] += [str(k).strip() for k in p.get("keywords", []) if str(k).strip() not in entry["keywords"]]
        entry["files"] = sorted(set(entry["files"]) | set(files))
    return sorted(out.values(), key=lambda p: -len(p["files"]))


def _merge_into(target: Subject, p: dict) -> None:
    for n in [p["name"], *p["aliases"]]:
        if norm(n) not in map(norm, target.names()):
            target.aliases.append(n)
    target.keywords += [k for k in p["keywords"] if k not in target.keywords]
    if not target.description:
        target.description = p["description"]


def review(proposals: list[dict], snippets: list[dict], registry: Registry, ask=input) -> Registry:
    """Walk through the proposals with the user. Returns a modified copy of the registry."""
    staged = copy.deepcopy(registry)
    for i, p in enumerate(proposals, start=1):
        files = [snippets[k - 1]["name"] for k in p["files"]]
        print()
        print(c(f"{i}/{len(proposals)}  {p['name']}", "bold") + f"  ({len(files)} of the sampled files)")
        if p["aliases"]:
            print(f"   aliases:  {', '.join(p['aliases'])}")
        print(f"   keywords: {', '.join(p['keywords'])}")
        print(c(f"   e.g.      {', '.join(files[:4])}" + (" …" if len(files) > 4 else ""), "dim"))

        existing = staged.get(p["name"]) or next(filter(None, map(staged.get, p["aliases"])), None)
        near, score = staged.closest(p["name"])
        similar = existing or (near if score >= SIMILAR else None)
        if similar:
            print(f"   This looks like {c(similar.name, 'bold')}, which you already have.")
        default = "m" if similar else "k"
        while True:
            try:
                choice = (ask(f"   [k]eep · [r]ename · [m]erge into another · [d]rop  [{default}] ").strip().lower()
                          or default)[:1]
            except EOFError:
                choice = "d"
            if choice in "krmd":
                break
        if choice == "d":
            continue
        if choice == "m":
            names = [s.name for s in staged.subjects]
            if not names:
                print("   Nothing to merge into yet; keeping it.")
                choice = "k"
            else:
                for j, n in enumerate(names, start=1):
                    print(f"     {j}. {n}")
                d = str(names.index(similar.name) + 1) if similar else ""
                try:
                    answer = ask(f"     merge into which? [{d}] ").strip() or d
                except EOFError:
                    answer = ""
                target = (staged.subjects[int(answer) - 1] if answer.isdigit() and 1 <= int(answer) <= len(names)
                          else staged.get(answer))
                if target is None:
                    print("   Not a subject; dropping this proposal.")
                    continue
                _merge_into(target, p)
                continue
        name = p["name"]
        if choice == "r":
            try:
                name = ask("   new name: ").strip() or name
            except EOFError:
                pass
            if norm(name) != norm(p["name"]):
                p = {**p, "aliases": [p["name"], *p["aliases"]]}
        if staged.get(name):
            _merge_into(staged.get(name), p)
        else:
            staged.add(Subject(name, aliases=[a for a in p["aliases"] if norm(a) != norm(name)],
                               description=p["description"], keywords=p["keywords"]))
    return staged


def show_registry(reg: Registry) -> str:
    rows = [[s.name, s.folder, ", ".join(s.aliases), ", ".join(s.keywords)] for s in reg.subjects]
    return table(["subject", "folder", "aliases", "keywords"], rows)
