"""Read the start of a file and return its text.

Everything heavy is delegated to system tools: poppler (pdftotext, pdftoppm)
for PDFs, tesseract for OCR, LibreOffice for old .ppt/.doc. Modern Office
files are just zipped XML, so those are read with the standard library.
"""

from __future__ import annotations

import posixpath
import re
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

# Bump when extraction logic changes, so cached results are recomputed.
EXTRACT_VERSION = 2

# A PDF page with less text than this is treated as a scan and OCR'd.
SCAN_THRESHOLD = 50
OCR_DPI = 200

PDF = {".pdf"}
PPTX = {".pptx"}
DOCX = {".docx"}
LEGACY = {".ppt": "pptx", ".doc": "docx"}
IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}
PLAIN = {".txt", ".md"}
NOTEBOOK = {".ipynb"}
SUPPORTED = PDF | PPTX | DOCX | set(LEGACY) | IMAGES | PLAIN | NOTEBOOK

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


class ExtractError(Exception):
    pass


def clean(text: str) -> str:
    """Collapse runs of spaces and drop blank lines."""
    lines = (re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _run(cmd: list[str], timeout: float = 120) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError as e:
        raise ExtractError(f"{cmd[0]} is not installed") from e
    except subprocess.TimeoutExpired as e:
        raise ExtractError(f"{cmd[0]} timed out") from e
    if out.returncode != 0:
        msg = out.stderr.decode(errors="replace").strip().splitlines()
        raise ExtractError(f"{cmd[0]} failed: {msg[-1] if msg else out.returncode}")
    return out.stdout.decode(errors="replace")


def ocr_image(path: Path) -> str:
    return _run(["tesseract", str(path), "stdout", "-l", "eng"], timeout=180)


# --- PDF ---------------------------------------------------------------------

def _pdf(path: Path, max_pages: int) -> dict:
    raw = _run(["pdftotext", "-f", "1", "-l", str(max_pages), "-enc", "UTF-8", str(path), "-"])
    # pdftotext ends every page with a form feed.
    pages = raw.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    ocr_pages = 0
    with tempfile.TemporaryDirectory(prefix="sloth-ocr-") as tmp:
        for i, page in enumerate(pages, start=1):
            if len(re.sub(r"\s", "", page)) >= SCAN_THRESHOLD:
                continue
            prefix = Path(tmp) / f"p{i}"
            _run(["pdftoppm", "-r", str(OCR_DPI), "-f", str(i), "-l", str(i),
                  "-gray", "-png", "-singlefile", str(path), str(prefix)])
            pages[i - 1] = ocr_image(prefix.with_suffix(".png"))
            ocr_pages += 1
    if ocr_pages == 0:
        method = "pdf-text"
    elif ocr_pages == len(pages):
        method = "pdf-ocr"
    else:
        method = "pdf-text+ocr"
    return {"text": "\n".join(pages), "pages_read": len(pages), "method": method, "is_scan": ocr_pages > 0}


# --- Office Open XML (zip of XML parts) --------------------------------------

def _xml(z: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(z.read(name))


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """Map relationship id -> (type, absolute part name) for a part."""
    folder, file = posixpath.split(part)
    rels_name = posixpath.join(folder, "_rels", file + ".rels")
    if rels_name not in z.namelist():
        return {}
    out = {}
    for rel in _xml(z, rels_name).findall("rel:Relationship", NS):
        target = posixpath.normpath(posixpath.join(folder, rel.get("Target", "")))
        out[rel.get("Id")] = (rel.get("Type", ""), target)
    return out


def _paragraphs(root: ET.Element) -> list[str]:
    paras = []
    for p in root.iter(f"{{{NS['a']}}}p"):
        text = "".join(t.text or "" for t in p.iter(f"{{{NS['a']}}}t")).strip()
        if text:
            paras.append(text)
    return paras


def _pptx(path: Path, max_slides: int) -> dict:
    with zipfile.ZipFile(path) as z:
        pres = "ppt/presentation.xml"
        rels = _rels(z, pres)
        # Slide order comes from presentation.xml, not from the part names.
        order = [
            rels[s.get(f"{{{NS['r']}}}id")][1]
            for s in _xml(z, pres).iterfind("p:sldIdLst/p:sldId", NS)
        ]
        chunks = []
        for n, slide in enumerate(order[:max_slides], start=1):
            chunks.append(f"[Slide {n}]")
            chunks.extend(_paragraphs(_xml(z, slide)))
            for rtype, target in _rels(z, slide).values():
                if rtype.endswith("/notesSlide"):
                    # Skip the slide-number field that notes pages carry.
                    notes = [t for t in _paragraphs(_xml(z, target)) if not t.isdigit()]
                    if notes:
                        chunks.append("Notes: " + " ".join(notes))
    return {"text": "\n".join(chunks), "pages_read": min(len(order), max_slides),
            "method": "pptx", "is_scan": False}


def _docx(path: Path, max_chars: int) -> dict:
    with zipfile.ZipFile(path) as z:
        body = _xml(z, "word/document.xml")
    paras, total = [], 0
    for p in body.iter(f"{{{NS['w']}}}p"):
        text = "".join(t.text or "" for t in p.iter(f"{{{NS['w']}}}t")).strip()
        if text:
            paras.append(text)
            total += len(text) + 1
            if total >= max_chars:
                break
    text = "\n".join(paras)[:max_chars]
    return {"text": text, "pages_read": 1, "method": "docx", "is_scan": False}


def _legacy(path: Path, target: str, opts: dict) -> dict:
    office = shutil.which("soffice") or shutil.which("libreoffice")
    if not office:
        raise ExtractError("LibreOffice is not installed; skipping old Office format")
    with tempfile.TemporaryDirectory(prefix="sloth-lo-") as tmp:
        # A private profile, so this works even while LibreOffice is open.
        profile = Path(tmp, "profile").as_uri()
        _run([office, f"-env:UserInstallation={profile}", "--headless",
              "--convert-to", target, "--outdir", tmp, str(path)], timeout=180)
        converted = Path(tmp, path.stem + "." + target)
        if not converted.exists():
            raise ExtractError(f"LibreOffice could not convert {path.name}")
        result = _pptx(converted, opts["max_slides"]) if target == "pptx" else _docx(converted, opts["docx_chars"])
    result["method"] = f"{path.suffix.lower()[1:]}->{result['method']}"
    return result


# --- plain text and Jupyter notebooks -----------------------------------------

def _plain(path: Path, max_chars: int) -> dict:
    with open(path, "rb") as f:
        head = f.read(max_chars * 4)
    if b"\x00" in head:
        raise ExtractError("looks like a binary file")
    return {"text": head.decode("utf-8", errors="replace"), "pages_read": 1, "method": "text", "is_scan": False}


def _notebook(path: Path, max_chars: int) -> dict:
    """Markdown and code cells in order; outputs (often huge images) are skipped."""
    import json

    try:
        nb = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise ExtractError(f"not a valid notebook: {e}") from None
    chunks, total = [], 0
    for cell in nb.get("cells", []):
        src = cell.get("source", "")
        src = "".join(src) if isinstance(src, list) else str(src)
        if src.strip():
            chunks.append(src)
            total += len(src)
            if total >= max_chars:
                break
    return {"text": "\n".join(chunks), "pages_read": len(chunks), "method": "ipynb", "is_scan": False}


# --- entry point -------------------------------------------------------------

def extract(path: Path | str, *, max_pages: int = 3, max_slides: int = 8,
            max_chars: int = 3000, docx_chars: int = 2000) -> dict:
    """Return {text, pages_read, method, is_scan, chars, seconds, error}.

    Never raises for a bad file: problems are reported in "error" so one
    broken download can't stop a whole run.
    """
    path = Path(path)
    ext = path.suffix.lower()
    start = time.perf_counter()
    try:
        if ext in PDF:
            result = _pdf(path, max_pages)
        elif ext in PPTX:
            result = _pptx(path, max_slides)
        elif ext in DOCX:
            result = _docx(path, docx_chars)
        elif ext in LEGACY:
            result = _legacy(path, LEGACY[ext], {"max_slides": max_slides, "docx_chars": docx_chars})
        elif ext in PLAIN:
            result = _plain(path, max_chars)
        elif ext in NOTEBOOK:
            result = _notebook(path, max_chars)
        elif ext in IMAGES:
            result = {"text": ocr_image(path), "pages_read": 1, "method": "image-ocr", "is_scan": True}
        else:
            result = {"text": "", "pages_read": 0, "method": "unsupported", "is_scan": False}
        result["error"] = None
    except (ExtractError, zipfile.BadZipFile, ET.ParseError, KeyError) as e:
        result = {"text": "", "pages_read": 0, "method": "error", "is_scan": False, "error": str(e)}
    result["text"] = clean(result["text"])[:max_chars]
    result["chars"] = len(result["text"])
    result["seconds"] = round(time.perf_counter() - start, 3)
    return result


def extract_cached(path: Path, cfg, cache) -> dict:
    """extract() with results cached by file content. Adds "sha256" and "cached"."""
    from .cache import sha256

    sha = sha256(path)
    opts = {"max_pages": cfg.max_pages, "max_slides": cfg.max_slides, "max_chars": cfg.max_chars}
    key = f"{sha}:v{EXTRACT_VERSION}:" + ":".join(f"{k}={v}" for k, v in opts.items())
    result = cache.get("extract", key)
    if result is not None:
        return {**result, "sha256": sha, "cached": True}
    result = extract(path, **opts)
    if not result["error"]:  # a missing tool shouldn't be remembered forever
        cache.put("extract", key, sha, result)
    return {**result, "sha256": sha, "cached": False}
