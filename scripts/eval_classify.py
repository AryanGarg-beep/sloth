#!/usr/bin/env python3
"""Classify test_docs/ and score the result against tests/expected.json.

    python scripts/eval_classify.py                       # configured model, cached answers reused
    python scripts/eval_classify.py --fresh               # ask the model again (for timing)
    python scripts/eval_classify.py --model gemma4:26b-a4b-it-qat --out results.json

Nothing is moved; this only reads files and asks the local model.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sloth import classify as C  # noqa: E402
from sloth import config  # noqa: E402
from sloth.cache import Cache  # noqa: E402
from sloth.extract import extract_cached  # noqa: E402
from sloth.ollama import Ollama  # noqa: E402
from sloth.registry import Registry, similarity  # noqa: E402
from sloth.ui import c, table  # noqa: E402


def score(exp: dict, got: dict, registry: Registry) -> dict:
    """Where would the file end up, and is that where it should?"""
    if not exp["is_college"]:
        ok = got["status"] in (C.NOT_COLLEGE, C.UNREADABLE)
        return {"route_ok": ok, "subject_ok": ok, "unit_ok": None, "needs_look": got["status"] == C.UNSURE}
    if registry.get(exp["subject"]):
        subject_ok = got["status"] == C.SORTED and got["subject"] == exp["subject"]
    else:  # a subject the registry doesn't have yet: it should be proposed
        proposed = (got.get("new_subject") or {}).get("name", "")
        subject_ok = got["status"] == C.NEW and similarity(proposed, exp["subject"]) >= 0.8
    unit_ok = got["unit"] == exp["unit"]
    return {"route_ok": subject_ok and unit_ok, "subject_ok": subject_ok, "unit_ok": unit_ok,
            "needs_look": got["status"] == C.UNSURE}


def describe(got: dict) -> str:
    s = got["status"]
    if s == C.SORTED:
        return f"{got['subject']} / {got['unit'] or '-'}"
    if s == C.NEW:
        return f"NEW {got['new_subject']['name']} / {got['unit'] or '-'}"
    if s == C.ASK:
        return f"{got['new_subject']['name']}? (≈{got['suggestion']})"
    if s == C.UNSURE:
        return f"needs a look ({got['subject'] or '?'})"
    return s.replace("_", " ")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs", type=Path, default=ROOT / "test_docs")
    ap.add_argument("--key", type=Path, default=ROOT / "tests" / "expected.json")
    ap.add_argument("--registry", type=Path, default=ROOT / "tests" / "fixtures" / "subjects.json")
    ap.add_argument("--model", help="override the configured model")
    ap.add_argument("--fresh", action="store_true", help="ignore cached model answers")
    ap.add_argument("--out", type=Path, help="write per-file results as JSON")
    args = ap.parse_args()

    cfg = config.load()
    if args.model:
        cfg.model = args.model
    registry = Registry.load(args.registry)
    key = json.loads(args.key.read_text())
    ollama = Ollama(cfg.host)
    cache = Cache(cfg.cache_path)

    # Load the model once so the first file's time isn't mostly load time.
    t = time.perf_counter()
    ollama.chat(cfg.model, [{"role": "user", "content": "hi"}], options={"num_predict": 1})
    load_s = time.perf_counter() - t

    rows, results = [], []
    for name in sorted(key, key=str.casefold):
        path = args.docs / name
        ex = extract_cached(path, cfg, cache)
        got = C.classify(path, ex, registry, ollama, cfg, cache=None if args.fresh else cache)
        exp = key[name]
        s = score(exp, got, registry)
        results.append({"file": name, "expected": exp, "got": got, "score": s,
                        "extract_s": ex["seconds"], "extract_method": ex["method"], "is_scan": ex["is_scan"]})
        mark = c("✓", "green") if s["route_ok"] else (c("?", "yellow") if s["needs_look"] else c("✗", "red"))
        expected = f"{exp['subject']} / {exp['unit'] or '-'}" if exp["is_college"] else "not college"
        rows.append([mark, name, expected, describe(got), f"{got['confidence']:.2f}",
                     f"{got['model_s']:.1f}s" + (" (c)" if got["cached"] else ""),
                     got["reason"] + (f" [{got['unit_note']}]" if got.get("unit_note") else "")])
    cache.close()

    print(table(["", "file", "expected", "sloth says", "conf", "model", "reason"], rows,
                title=f"{cfg.model} on {len(rows)} files (threshold {cfg.confidence_threshold})"))

    n = len(results)
    college = [r for r in results if r["expected"]["is_college"]]
    route_ok = sum(r["score"]["route_ok"] for r in results)
    look = sum(r["score"]["needs_look"] for r in results)
    wrong = [r for r in results if not r["score"]["route_ok"] and not r["score"]["needs_look"]]
    subj_ok = sum(r["score"]["subject_ok"] for r in college)
    unit_ok = sum(bool(r["score"]["unit_ok"]) for r in college)
    model_times = [r["got"]["model_s"] for r in results if r["got"]["attempts"] and not r["got"]["cached"]]
    text_ex = [r["extract_s"] for r in results if not r["is_scan"] and r["extract_method"] not in ("unsupported", "error")]
    ocr_ex = [r["extract_s"] for r in results if r["is_scan"]]

    print()
    print(f"correct destination : {route_ok}/{n} ({route_ok / n:.0%})")
    print(f"  college subject   : {subj_ok}/{len(college)}    unit: {unit_ok}/{len(college)}")
    print(f"needs a look        : {look}")
    print(f"wrongly filed       : {len(wrong)}" + (f"  ({', '.join(r['file'] for r in wrong)})" if wrong else ""))
    print(f"model load          : {load_s:.1f}s (once)")
    if model_times:
        print(f"model per file      : mean {statistics.mean(model_times):.1f}s, "
              f"median {statistics.median(model_times):.1f}s, max {max(model_times):.1f}s ({len(model_times)} calls)")
    else:
        print("model per file      : all answers cached (use --fresh to time the model)")
    if text_ex:
        print(f"extract, text files : mean {statistics.mean(text_ex):.2f}s")
    if ocr_ex:
        print(f"extract, OCR files  : mean {statistics.mean(ocr_ex):.2f}s")
    retries = sum(1 for r in results if r["got"]["attempts"] == 2)
    print(f"retries             : {retries}")

    if args.out:
        args.out.write_text(json.dumps({"model": cfg.model, "results": results}, indent=2, ensure_ascii=False))
        print(c(f"wrote {args.out}", "dim"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
