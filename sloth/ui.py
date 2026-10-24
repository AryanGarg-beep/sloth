"""Tiny terminal helpers: colour and tables, no dependencies."""

from __future__ import annotations

import os
import re
import shutil
import sys

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_CODES = {"red": "31", "green": "32", "yellow": "33", "blue": "34", "dim": "2", "bold": "1"}


def use_colour() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def c(text: str, style: str) -> str:
    if not use_colour():
        return text
    return f"\x1b[{_CODES[style]}m{text}\x1b[0m"


def _width(s: str) -> int:
    return len(_ANSI.sub("", s))


def _pad(s: str, n: int) -> str:
    return s + " " * (n - _width(s))


def _truncate(s: str, n: int) -> str:
    if _width(s) <= n:
        return s
    plain = _ANSI.sub("", s)
    return plain[: max(n - 1, 0)] + "…"


def table(headers: list[str], rows: list[list[str]], title: str | None = None) -> str:
    """Render rows as an aligned table. The last column shrinks to fit the terminal."""
    rows = [[str(cell) for cell in row] for row in rows]
    widths = [max([_width(h)] + [_width(r[i]) for r in rows]) for i, h in enumerate(headers)]
    term = shutil.get_terminal_size((120, 20)).columns
    used = sum(widths[:-1]) + 3 * (len(widths) - 1)
    widths[-1] = max(10, min(widths[-1], term - used))

    def line(cells: list[str]) -> str:
        return " │ ".join(_pad(_truncate(cell, w), w) for cell, w in zip(cells, widths)).rstrip()

    out = []
    if title:
        out.append(c(title, "bold"))
    out.append(c(line(headers), "bold"))
    out.append("─┼─".join("─" * w for w in widths))
    out.extend(line(r) for r in rows)
    return "\n".join(out)
