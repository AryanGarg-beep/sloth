"""Ask Gemma which subject and unit a file belongs to, then decide what to do with it.

The model only answers questions about the text. Everything that decides where
a file actually goes (registry lookup, fuzzy "did you mean", unit from the
filename, the confidence threshold) is plain Python in decide(), so it is
predictable and testable.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from .registry import SIMILAR, Registry
from .units import MAX_UNIT, unit_from_filename, units_in_text

PROMPT_VERSION = 1
DOC_TYPES = ["notes", "slides", "assignment", "question_paper", "lab", "syllabus", "other"]

# Outcomes of decide():
SORTED = "sorted"            # known subject, confident: move it
NEW = "new_subject"          # college material for a subject not in the registry yet
ASK = "did_you_mean"         # proposed new subject looks like an existing one
UNSURE = "unsure"            # goes to "_Needs a look"
NOT_COLLEGE = "not_college"  # left alone
UNREADABLE = "unreadable"    # no text and not a type we read: left alone

SCHEMA = {
    "type": "object",
    "properties": {
        # Reason comes first so the model states its evidence before deciding.
        "reason": {"type": "string"},
        "is_college": {"type": "boolean"},
        "subject": {"type": ["string", "null"]},
        "new_subject": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "keywords": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "keywords"],
                },
            ]
        },
        # Quoting the phrase first makes the model actually look for it.
        "unit_evidence": {"type": ["string", "null"]},
        "unit": {"type": ["integer", "null"]},
        "doc_type": {"type": "string", "enum": DOC_TYPES},
        "confidence": {"type": "number"},
    },
    "required": ["reason", "is_college", "subject", "new_subject", "unit_evidence", "unit", "doc_type", "confidence"],
}

SYSTEM = """You sort a university student's downloaded files into subject folders.
You get the list of known subjects, a filename, and text extracted from the start of the file.
OCR text may contain recognition errors; read through them.

Answer with JSON:
- reason: one short sentence with the evidence you used (topics, headings, course title).
- is_college: true only for study material for a course: lecture notes, slides, handouts, \
assignments, tutorial sheets, practice problems, lab manuals, question papers, syllabi. \
Receipts, tickets, bills, rent or bank documents, forms, memes, screenshots, personal files \
and software are false, even if their filename contains words like "unit".
- subject: the exact "name" of the known subject the file belongs to, judged mainly from the \
text's topics and course title; abbreviations and aliases in the filename can help. \
null if it is not college material or fits none of the known subjects.
- new_subject: only if it is clearly college material for a subject that is NOT in the list: \
a short conventional course name (e.g. "Operating Systems") and 3-8 topic keywords. Otherwise null. \
Never fill both subject and new_subject.
- unit_evidence: copy the exact phrase from the TEXT (not the filename) that names the unit, \
module or chapter of the whole document, usually in the title or first heading, e.g. \
"Unit 4 Tutorial Sheet", "Module 3", "Chapter II". null if the text names none, or names several \
different units (a syllabus, a question paper covering units 1-3).
- unit: the number in unit_evidence as an integer (roman numerals converted), else null.
- doc_type: notes, slides, assignment (also tutorial sheets and practice problems), \
question_paper, lab, syllabus or other.
- confidence: 0 to 1, how sure you are about is_college and the subject together. \
Below 0.6 if the text is unreadable, very short or ambiguous."""


def build_messages(filename: str, extraction: dict, registry: Registry) -> list[dict]:
    subjects = json.dumps(registry.for_prompt(), ensure_ascii=False, indent=1) if registry.subjects else "(none yet)"
    if extraction["text"]:
        how = f"{extraction['method']}, {extraction['pages_read']} page(s)"
        body = f"Extracted text ({how}):\n<<<\n{extraction['text']}\n>>>"
    else:
        body = "No text could be extracted from this file. Judge from the filename alone and keep confidence low."
    user = f"Known subjects:\n{subjects}\n\nFilename: {filename}\n\n{body}"
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def validate(raw: dict) -> dict:
    """Check the model's JSON. Raises ValueError so the caller can retry."""
    if not isinstance(raw, dict):
        raise ValueError("reply is not a JSON object")
    missing = [k for k in SCHEMA["required"] if k not in raw]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    if not isinstance(raw["is_college"], bool):
        raise ValueError("is_college must be true or false")
    for k in ("subject",):
        if raw[k] is not None and not isinstance(raw[k], str):
            raise ValueError(f"{k} must be a string or null")
    ns = raw["new_subject"]
    if ns is not None:
        if not isinstance(ns, dict) or not isinstance(ns.get("name"), str) or not ns["name"].strip():
            raise ValueError("new_subject must be null or {name, keywords}")
        ns["keywords"] = [str(k) for k in ns.get("keywords") or []]
    unit = raw["unit"]
    if isinstance(unit, float) and unit.is_integer():
        unit = int(unit)
    if unit is not None and (not isinstance(unit, int) or isinstance(unit, bool)):
        raise ValueError("unit must be an integer or null")
    raw["unit"] = unit if unit is None or 1 <= unit <= MAX_UNIT else None
    if raw["doc_type"] not in DOC_TYPES:
        raw["doc_type"] = "other"
    try:
        raw["confidence"] = min(1.0, max(0.0, float(raw["confidence"])))
    except (TypeError, ValueError):
        raise ValueError("confidence must be a number") from None
    if raw.get("unit_evidence") is not None:
        raw["unit_evidence"] = str(raw["unit_evidence"])
    raw["reason"] = str(raw["reason"]).strip()
    return raw


def _squash(s: str) -> str:
    """Letters and digits only, so "FLA • Unit 1" matches "FLA · Unit 1"."""
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))


def content_unit(raw: dict, text: str) -> tuple[int | None, str | None]:
    """The model's unit, but only if its quoted evidence is really in the text
    and the document isn't one that spans several units. Returns (unit, why_not)."""
    unit, evidence = raw["unit"], raw.get("unit_evidence")
    if unit is None:
        return None, None
    quote = f" {_squash(evidence or '')} "
    if not quote.strip() or quote not in f" {_squash(text)} ":
        return None, "the unit the model quoted isn't in the text"
    if unit not in units_in_text(evidence):
        return None, f"the quote {evidence!r} doesn't name a unit, module or chapter {unit}"
    if len(units_in_text(text)) > 1:
        head = f" {_squash(' '.join(text.splitlines()[:2]))} "
        if quote not in head:
            return None, "the text mentions several units"
    return unit, None


def decide(raw: dict, filename: str, text: str, registry: Registry, threshold: float) -> dict:
    """Turn a validated model answer into what sloth will do with the file."""
    out = {
        "status": None, "subject": None, "suggestion": None, "new_subject": None,
        "unit": None, "unit_source": None, "unit_note": None, "doc_type": raw["doc_type"],
        "confidence": raw["confidence"], "reason": raw["reason"],
    }
    if not raw["is_college"]:
        out["status"] = NOT_COLLEGE
        return out

    out["doc_type"] = raw["doc_type"]
    subject = registry.get(raw["subject"])
    proposed = raw["new_subject"]
    if raw["subject"] and not subject and not proposed:
        # The model named a subject that isn't in the registry: treat it as a proposal.
        proposed = {"name": raw["subject"], "keywords": []}

    if subject:
        out["status"], out["subject"] = SORTED, subject.name
    elif proposed:
        existing = registry.get(proposed["name"])
        near, score = registry.closest(proposed["name"])
        if existing:
            out["status"], out["subject"] = SORTED, existing.name
        elif near and score >= SIMILAR:
            out["status"], out["suggestion"], out["new_subject"] = ASK, near.name, proposed
        else:
            out["status"], out["new_subject"] = NEW, proposed
    else:
        out["status"] = UNSURE

    # Unit: the filename is cheap and usually right; the content wins when they disagree.
    from_name = unit_from_filename(filename)
    from_text, why_not = content_unit(raw, text)
    if why_not:
        out["unit_note"] = f"ignored model's unit {raw['unit']}: {why_not}"
    if from_text is not None:
        out["unit"], out["unit_source"] = from_text, "content"
        if from_name is not None and from_name != from_text:
            out["reason"] += f" [Filename says unit {from_name} but the content says unit {from_text}; trusting the content.]"
        elif from_name == from_text:
            out["unit_source"] = "filename+content"
    elif from_name is not None and raw["doc_type"] not in ("syllabus", "question_paper"):
        out["unit"], out["unit_source"] = from_name, "filename"

    if out["status"] in (SORTED, NEW) and out["confidence"] < threshold:
        out["status"] = UNSURE  # keep the best guess so the plan can show it
    return out


def _cache_key(sha: str, filename: str, model: str, registry: Registry) -> str:
    reg = hashlib.sha256(json.dumps(registry.for_prompt(), sort_keys=True).encode()).hexdigest()[:16]
    return f"{sha}:{model}:p{PROMPT_VERSION}:r{reg}:{filename}"


def ask_model(filename: str, extraction: dict, registry: Registry, ollama, model: str) -> dict:
    """Call the model, validate, retry once on a bad reply.

    Returns {"raw": validated answer or None, "error": str | None, "model_s": float, "attempts": int}.
    """
    messages = build_messages(filename, extraction, registry)
    start, error = time.perf_counter(), None
    for attempt in (1, 2):
        try:
            content, _ = ollama.chat(model, messages, schema=SCHEMA)
        except OSError as e:
            return {"raw": None, "error": f"Ollama call failed: {e}", "model_s": time.perf_counter() - start,
                    "attempts": attempt}
        try:
            raw = validate(json.loads(content))
            if not extraction["text"]:
                raw["confidence"] = min(raw["confidence"], 0.5)
            return {"raw": raw, "error": None, "model_s": round(time.perf_counter() - start, 3), "attempts": attempt}
        except (json.JSONDecodeError, ValueError) as e:
            error = f"invalid reply ({e})"
            messages = messages + [
                {"role": "assistant", "content": content},
                {"role": "user", "content": f"That reply was invalid: {e}. Answer again with JSON that follows the schema."},
            ]
    return {"raw": None, "error": error, "model_s": round(time.perf_counter() - start, 3), "attempts": 2}


def classify(path: Path, extraction: dict, registry: Registry, ollama, cfg, cache=None) -> dict:
    """Full classification of one file: model answer (cached) + decision.

    Returns the decide() fields plus "model_s", "cached", "attempts" and "error".
    """
    if extraction["method"] in ("unsupported", "error"):
        return {"status": UNREADABLE, "subject": None, "suggestion": None, "new_subject": None,
                "unit": None, "unit_source": None, "unit_note": None, "doc_type": None, "confidence": 0.0,
                "reason": extraction["error"] or "not a document type sloth reads",
                "model_s": 0.0, "cached": False, "attempts": 0, "error": extraction["error"], "raw": None}

    key = _cache_key(extraction["sha256"], path.name, cfg.model, registry) if cache else None
    answer = cache.get("classify", key) if cache else None
    cached = answer is not None
    if not cached:
        answer = ask_model(path.name, extraction, registry, ollama, cfg.model)
        if cache and answer["raw"] is not None:
            cache.put("classify", key, extraction["sha256"], answer)

    if answer["raw"] is None:
        result = {"status": UNSURE, "subject": None, "suggestion": None, "new_subject": None,
                  "unit": unit_from_filename(path.name), "unit_source": "filename", "unit_note": None, "doc_type": None,
                  "confidence": 0.0, "reason": answer["error"]}
    else:
        result = decide(dict(answer["raw"]), path.name, extraction["text"], registry, cfg.confidence_threshold)
    return {**result, "model_s": answer["model_s"], "cached": cached, "attempts": answer["attempts"],
            "error": answer["error"], "raw": answer["raw"]}
