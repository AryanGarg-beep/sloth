"""The subject registry: what subjects exist and how to recognise them.

Stored as JSON, a list of {name, aliases, description, keywords, folder}.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

# A proposed new subject this similar to an existing one becomes a
# "did you mean ...?" question instead of a new folder.
SIMILAR = 0.85

_STOPWORDS = {"and", "of", "the", "to", "in", "for", "&"}


@dataclass
class Subject:
    name: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    folder: str = ""

    def __post_init__(self):
        if not self.folder:
            self.folder = safe_folder(self.name)

    def names(self) -> list[str]:
        return [self.name, *self.aliases]


def safe_folder(name: str) -> str:
    """A folder name that's safe on any filesystem."""
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]', " ", name)
    return re.sub(r"\s+", " ", name).strip(" .") or "Unnamed"


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def acronym(s: str) -> str:
    return "".join(w[0] for w in norm(s).split() if w not in _STOPWORDS)


def similarity(a: str, b: str) -> float:
    """0..1. Acronyms count as a full match: "FLA" ~ "Formal Languages and Automata"."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na.replace(" ", "") in {acronym(b)} or nb.replace(" ", "") in {acronym(a)}:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


class Registry:
    def __init__(self, path: Path, subjects: list[Subject] | None = None):
        self.path = path
        self.subjects = subjects or []

    @classmethod
    def load(cls, path: Path) -> "Registry":
        if not path.exists():
            return cls(path, [])
        data = json.loads(path.read_text())
        return cls(path, [Subject(**s) for s in data.get("subjects", [])])

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"subjects": [asdict(s) for s in self.subjects]}, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(self.path)

    def get(self, name: str | None) -> Subject | None:
        """Exact (case/punctuation-insensitive) match on name, alias or folder."""
        if not name:
            return None
        key = norm(name)
        for s in self.subjects:
            if key in {norm(n) for n in s.names()} or key == norm(s.folder):
                return s
        return None

    def closest(self, name: str) -> tuple[Subject | None, float]:
        best, score = None, 0.0
        for s in self.subjects:
            for n in s.names():
                sc = similarity(name, n)
                if sc > score:
                    best, score = s, sc
        return best, score

    def add(self, subject: Subject) -> None:
        if self.get(subject.name):
            raise ValueError(f"subject {subject.name!r} already exists")
        self.subjects.append(subject)

    def for_prompt(self) -> list[dict]:
        return [
            {"name": s.name, "aliases": s.aliases, "description": s.description, "keywords": s.keywords}
            for s in self.subjects
        ]
