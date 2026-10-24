#!/usr/bin/env python3
"""Summarise a saved plan as numbers (no filenames, no contents), for the post.

    python scripts/plan_stats.py ~/sloth_trial/.sloth/state/plans/plan-XXXX.json
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path


def mean(xs):
    return f"{statistics.mean(xs):.1f}s" if xs else "n/a"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    plan = json.loads(Path(sys.argv[1]).expanduser().read_text())
    items = plan["items"]
    n = len(items)
    status = Counter(it["status"] for it in items)
    readable = [it for it in items if it["status"] != "unreadable"]
    called = [it for it in readable if not it["cached"]]
    text = [it for it in called if not it["extract_method"].endswith("ocr") and "ocr" not in it["extract_method"]]
    ocr = [it for it in called if "ocr" in it["extract_method"]]

    def pct(k):
        return f"{status[k]} ({status[k] / n:.0%})" if n else "0"

    college = status["sorted"] + status["new_subject"] + status["did_you_mean"] + status["unsure"]
    print(f"model                : {plan['model']}")
    print(f"files looked at      : {n} (+{len(plan['skipped'])} skipped: hidden/partial/etc.)")
    print(f"readable             : {len(readable)}")
    print(f"auto-sorted          : {pct('sorted')}")
    print(f"new-subject proposals: {status['new_subject'] + status['did_you_mean']}")
    print(f"needs a look         : {pct('unsure')}")
    print(f"left alone           : {status['not_college']} not college, {status['unreadable']} unreadable")
    if college:
        print(f"of college files     : {status['sorted'] / college:.0%} sorted automatically")
    print(f"time per file, text  : {mean([it['extract_s'] + it['model_s'] for it in text])} "
          f"(model {mean([it['model_s'] for it in text])}, n={len(text)})")
    print(f"time per file, OCR   : {mean([it['extract_s'] + it['model_s'] for it in ocr])} "
          f"(OCR {mean([it['extract_s'] for it in ocr])}, n={len(ocr)})")
    total = sum(it["extract_s"] + it["model_s"] for it in called)
    print(f"total work           : {total / 60:.1f} min for {len(called)} files")
    methods = Counter(it["extract_method"] for it in items)
    print(f"by type              : {', '.join(f'{k} {v}' for k, v in methods.most_common())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
