# sloth 🦥

A slow, careful, local file organizer for college material.

sloth reads what's *inside* your notes, slides and scanned handouts. It asks a local Gemma model (through [Ollama](https://ollama.com)) which subject and unit each file belongs to, and files it under:

```
~/Documents/College/<Subject>/Unit <N>/<original filename>
```

- **Nothing leaves your laptop.** sloth only talks to Ollama on `127.0.0.1`.
- **Nothing is deleted or overwritten.** Files are only moved. If a name is taken, the file becomes `name (2).pdf`.
- **Nothing moves until you say so.** Every run is a dry-run plan first.
- **Everything can be undone.** Every batch of moves is logged, and `sloth undo` puts the files back exactly where they were.

![The sloth web UI showing a plan: files grouped by subject and unit](post-assets/screenshots/ui-1-plan.png)

> Built for the DEV Hacktoberfest "Slow and Steady" challenge.

---

## Why

A semester leaves you with files called `unit 3.pdf`, `document(3).pdf`, `scan_0042.pdf` and `notes_final(1).pdf`, all in Downloads. The filename usually doesn't say which subject a file is for, and sometimes it's wrong about the unit. The content does say. sloth reads it.

## Install

sloth is **pure Python standard library: no pip dependencies.** The heavy lifting is done by command-line tools you install with your system package manager.

| Tool | Used for | Needed? |
|---|---|---|
| [Ollama](https://ollama.com) + a Gemma model | deciding subject and unit | yes |
| poppler (`pdftotext`, `pdftoppm`) | PDF text, and rendering scanned pages | yes |
| tesseract + English data | OCR for scans and photos | yes |
| LibreOffice (`soffice`) | old `.ppt` / `.doc` files | optional |

On Arch / CachyOS:

```sh
sudo pacman -S ollama poppler tesseract tesseract-data-eng   # + libreoffice-fresh for .ppt/.doc
ollama pull gemma4:e4b-it-qat

git clone <this repo> sloth && cd sloth
python -m venv .venv && source .venv/bin/activate
pip install -e .
sloth doctor
```

`sloth doctor` checks that Ollama is reachable, the model is pulled and every helper tool is found.

Python 3.11 or newer is required (for `tomllib`).

## Using it

### The web UI (easiest)

```sh
sloth ui            # opens http://127.0.0.1:8765
```

1. **Choose your folders.** Under *Sort files from*, add Downloads, Documents or any other folder. Under *into*, pick where sorted files go.
2. **Make a plan.** sloth reads each file, asks Gemma about it, and shows where every file *would* go. Nothing moves yet.
3. **Decide on new subjects.** If sloth finds a subject it doesn't know, you choose: create it, rename it, merge it into an existing subject, or skip it.
4. **Check and fix.** *Why?* shows the text Gemma saw, its exact answer and the rule sloth applied. *Fix* lets you pick a different subject or unit, send a file to "Needs a look", or leave it alone.
5. **Review & apply.** A confirmation lists every destination folder before anything moves.
6. **Undo** any batch from *History*.

| | |
|---|---|
| ![Why? dialog](post-assets/screenshots/ui-2-why.png) | ![Fixing one file](post-assets/screenshots/ui-3-fix.png) |
| ![Apply confirmation](post-assets/screenshots/ui-4-apply.png) | ![Undo](post-assets/screenshots/ui-6-undo.png) |
| ![Choosing folders](post-assets/screenshots/ui-9-folders.png) | ![Folder picker](post-assets/screenshots/ui-10-picker.png) |

The screenshots use the synthetic test files in `test_docs/`, not real data.

### The command line

```sh
sloth init                 # look at ~40 files and propose a subject list for you to review
sloth plan                 # classify the watched folders; prints the plan, moves nothing
sloth plan ~/Desktop -r    # a specific folder, including subfolders
sloth apply                # review the latest plan, answer y/N, move
sloth undo                 # move the last batch back   (--list, --batch ID, --skip-changed)
sloth explain "unit 3.pdf" # what sloth read from a file and why it decided
sloth subjects list        # also: add, alias, rename, merge
sloth extract file.pdf     # just show the extracted text (no model)
```

The CLI and the web UI share the same plan, journal and subject list. You can make a plan in one and apply it in the other.

## Where files go

| sloth's verdict | Destination |
|---|---|
| Known subject, unit found | `<target>/<Subject>/Unit <N>/` |
| Known subject, no single unit (syllabus, question paper, …) | `<target>/<Subject>/` |
| A subject you don't have yet | waits for your decision; it stays put until you decide |
| Confidence below 0.7, or unclear | `<target>/_Needs a look/` |
| Not college material (receipts, tickets, memes, installers) | **left where it is** |
| A type sloth can't read | **left where it is** |

Files sloth reads: PDF (text and scanned), PPTX, DOCX, PPT/DOC (through LibreOffice), JPG/PNG/WebP/TIFF/BMP (through OCR), TXT, MD, IPYNB.

Files sloth always skips: hidden files, partial downloads (`.crdownload`, `.part`, …), Office lock files (`~$…`, `.~lock…`), symlinks, and anything already inside the target folder.

## How it decides

```
file ──► extract text ──► Gemma (JSON schema, temperature 0) ──► Python rules ──► plan
         pdftotext / OCR       subject, unit, quoted evidence,       registry, units,
         zip+XML / soffice     doc type, confidence                  threshold
```

1. **Extract.** sloth reads the first 3 pages or 8 slides, up to 3,000 characters. A PDF page with almost no text is treated as a scan, rendered at 200 dpi and OCR'd.
2. **Ask.** Gemma gets the text, the filename and your subject list. It must answer in a fixed JSON schema: is it college material, which subject (or a proposed new one), the exact words that name the unit, the unit, the document type and its confidence.
3. **Check.** Gemma's answer is a suggestion; Python decides:
   - **Subject.** A subject Gemma names must exist in your list, matched by name or alias. A near-miss ("Discrete Mathematics" vs "Discrete Maths", or an acronym) becomes a "did you mean" instead of a duplicate folder.
   - **Unit evidence.** A unit from the content counts only if the quoted evidence really is in the text and names a *unit, module or chapter*. "Topic 4" or "Theme 5" don't count. Neither does a syllabus or question paper that covers several units.
   - **Filename vs content.** When the filename and the content disagree about the unit, the content wins, and `sloth explain` tells you so. The filename is used only when the content says nothing. It understands `unit 3`, `U3`, `mod 3`, `ch1`, `Unit-2`, `unit IV`, `Module 2Health` and similar.
   - **Low confidence.** Below 0.7, the file goes to `_Needs a look`.
4. **Cache.** Extracted text is cached by the file's SHA-256. Gemma's answers are cached by content, filename, model and subject list, so re-planning unchanged files is instant.

## Safety, in detail

These rules held from the first commit:

- **Moves only.** On Linux, moves use `renameat2(RENAME_NOREPLACE)`, so the kernel refuses to replace an existing file, even in a race. Across filesystems, sloth copies, verifies the SHA-256, links without clobbering, then removes the original.
- **Dry run by default.** `sloth plan` and *Make a plan* only read. Moving needs `sloth apply` plus a typed `y`, or the UI's confirmation dialog.
- **Re-checked at apply time.** If a file's size or modification time changed since planning, its SHA-256 is checked again. A file that changed or vanished is skipped.
- **Journal.** Every move is written to `journal.jsonl` before it happens ("pending") and after ("done"). If a batch is interrupted, undo reverses the finished moves and names the one that was in flight, so you can check it.
- **Careful undo.** Undo refuses to move a file back if it was edited after the move, or if something new now sits at its old path. It only removes empty folders that the same batch created.
- **Real-folder latch.** Until you set `allow_real_folders = true`, sloth refuses to read from or write to `~/Downloads` and `~/Documents`. Useful while you try it on a copy.
- **Local only.** The Ollama client refuses any host that isn't loopback. The web UI:
  - binds to `127.0.0.1` only
  - requires a per-run secret token on every change
  - rejects unexpected `Host` headers, which guards against DNS rebinding
  - sends a strict Content-Security-Policy
  - inserts filenames and document text as plain text, never as HTML

## Configuration

`~/.config/sloth/config.toml` is created on first run. The folder picker in the UI edits `watched` and `target_root` for you.

```toml
watched = ["/home/you/Downloads"]          # folders `sloth plan` scans
target_root = "/home/you/Documents/College"
allow_real_folders = false                 # the latch above

[model]
name = "gemma4:e4b-it-qat"
host = "http://127.0.0.1:11434"            # must be loopback

[classify]
confidence_threshold = 0.7                 # below this → "_Needs a look"

[extract]
max_pages = 3
max_slides = 8
max_chars = 3000
```

State is kept in `~/.local/share/sloth/`:
- `subjects.json`: your subject list, with aliases and keywords
- `cache.sqlite`
- `journal.jsonl`
- `plans/`

`SLOTH_CONFIG` and `SLOTH_HOME` point sloth somewhere else, which is handy for a trial run on a copy:

```sh
cp -a --reflink=auto ~/Downloads ~/sloth_trial/Downloads
SLOTH_CONFIG=~/sloth_trial/config.toml SLOTH_HOME=~/sloth_trial/state sloth ui
```

## Numbers

Measured on a laptop with an Intel Core Ultra 7 258V and 32 GB of RAM, using `gemma4:e4b-it-qat` on the CPU/iGPU through Ollama.

**Synthetic test set.** `test_docs/` has 26 made-up files: notes, slides, a scanned handout, a phone photo of a whiteboard, misleading filenames, receipts, a train ticket, a meme and an AppImage.

| | |
|---|---|
| correct subject + unit + decision | **26 / 26** |
| …with a subject list `sloth init` generated on its own | 26 / 26 |
| time per file (uncached) | ~6.2 s |

**A copy of a real Downloads folder.** It had 101 files at the top level.

| | |
|---|---|
| sorted automatically | 56 (92% of the files Gemma judged to be college material) |
| proposed as new subjects | 4 |
| sent to "Needs a look" | 1 |
| left alone: not college material | 10 |
| left alone: types sloth doesn't read (archives, installers, …) | 30 |
| time per file, text PDFs | ~8.5 s |
| time per file, scans (OCR ≈ 1.3 s of it) | ~9.4 s |
| `sloth init` (propose a subject list) | 57 s |

"Sorted automatically" isn't the same as "sorted correctly". The mistakes found on review were mostly about *what counts as a subject*: hackathon material filed as a course, and two networking subjects that should be one. That's what the new-subject review step and *Fix* are for.

## Credits and licences

sloth is MIT licensed. See [LICENSE](LICENSE).

It stands on:
- [Ollama](https://ollama.com) (MIT)
- [Gemma](https://ai.google.dev/gemma) (Gemma Terms of Use)
- [poppler](https://poppler.freedesktop.org) (GPL, run as a separate program, not linked)
- [Tesseract](https://github.com/tesseract-ocr/tesseract) (Apache-2.0)
- [LibreOffice](https://www.libreoffice.org) (MPL-2.0)
