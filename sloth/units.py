"""Find a unit number in a filename: "fla unit 1.pdf", "FLA_Unit2_x.pdf",
"TOC unit IV.pdf", "discrete mod 3.pdf", "ch1 relations.docx", "U-3.pdf"."""

from __future__ import annotations

import re

ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
         "xi": 11, "xii": 12}
MAX_UNIT = 20  # "ch 2026" is a year, not a chapter

_KEYWORD = r"(?<![a-z])(?:unit|module|mod|chapter|ch|u)"
# Digits may follow the keyword directly ("ch1", "Unit2") and run into the next
# word ("Module 2Health", from slide text). Roman numerals need a separator before
# and a non-letter after, so "Modi", "chi" and "unit ivory" don't count.
_PATTERN = re.compile(
    rf"{_KEYWORD}(?:[\s_\-.]*(?P<num>\d{{1,2}})(?!\d)|[\s_\-.]+(?P<roman>{'|'.join(sorted(ROMAN, key=len, reverse=True))})(?![a-z]))",
    re.IGNORECASE,
)


def _numbers(pattern: re.Pattern, s: str) -> list[int]:
    out = []
    for m in pattern.finditer(s):
        n = int(m["num"]) if m["num"] else ROMAN[m["roman"].lower()]
        if 1 <= n <= MAX_UNIT:
            out.append(n)
    return out


def unit_from_filename(name: str) -> int | None:
    found = _numbers(_PATTERN, name)
    return found[0] if found else None


# In running text only the full words count; "u 2" or "ch 3" in prose is noise.
_TEXT_PATTERN = re.compile(_PATTERN.pattern.replace("unit|module|mod|chapter|ch|u", "unit|module|chapter"),
                           re.IGNORECASE)


def units_in_text(text: str) -> set[int]:
    """Distinct unit numbers mentioned in a document's text."""
    return set(_numbers(_TEXT_PATTERN, text))
