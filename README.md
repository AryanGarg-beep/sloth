# sloth

A lazy, local file organizer for college material. sloth reads what's *inside* your notes, slides and scanned handouts, asks a local Gemma model (via [Ollama](https://ollama.com)) which subject and unit each one belongs to, and files it under:

```
~/Documents/College/<Subject>/Unit <N>/<original filename>
```

Nothing leaves your laptop. Nothing is deleted or overwritten. Nothing moves until you say so, and every batch can be undone.

> Work in progress for the DEV Hacktoberfest "Slow and Steady" challenge. Full docs coming.

## Quick start

sloth is pure Python standard library: no pip dependencies. The work is done by tools you probably already have:

```sh
sudo pacman -S ollama poppler tesseract tesseract-data-eng   # libreoffice optional, for old .ppt/.doc
ollama pull gemma4:e4b-it-qat

python -m venv ~/slow_and_steady/venv && source ~/slow_and_steady/venv/bin/activate
pip install -e .
sloth doctor
```

## Licence

MIT. See [LICENSE](LICENSE).
