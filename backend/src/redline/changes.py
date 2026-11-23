"""The change engine: resolve changes against one base text, apply them in one pass."""

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal


@dataclass(frozen=True, slots=True)
class Change:
    """Replace `text` with `replacement`.

    `occurrence` picks the nth match (1-based) or "all". Left as None, the match must be unique.
    """

    text: str
    replacement: str
    occurrence: int | Literal["all"] | None = None


@dataclass(frozen=True, slots=True)
class RangeChange:
    """Replace `text[start:end]` with `replacement`. An empty range inserts."""

    start: int
    end: int
    replacement: str


@dataclass(frozen=True, slots=True)
class Edit:
    """One tracked change: `removed`, found at `start` in the base text, becomes `inserted`."""

    change: int
    start: int
    removed: str
    inserted: str

    @property
    def end(self) -> int:
        return self.start + len(self.removed)


class ChangeError(ValueError):
    """A change that cannot be applied to this text. str(error) is "changes[i]: reason"."""

    def __init__(self, change: int, reason: str) -> None:
        super().__init__(f"changes[{change}]: {reason}")
        self.change = change
        self.reason = reason


def occurrences(text: str, needle: str) -> Iterator[int]:
    """Start offsets of the non-overlapping matches of needle, left to right (as str.replace)."""
    assert needle
    start = text.find(needle)
    while start != -1:
        yield start
        start = text.find(needle, start + len(needle))


def resolve(text: str, change: Change | RangeChange, index: int) -> list[Edit]:
    """The edits one change makes to text, or ChangeError."""
    if isinstance(change, RangeChange):
        start, end = change.start, change.end
        if not 0 <= start <= end <= len(text):
            reason = f"{start} to {end} is not a range within the document (0 to {len(text)})"
            raise ChangeError(index, reason)
        return [Edit(index, start, text[start:end], change.replacement)]
    if not change.text:
        raise ChangeError(index, "the text to replace is empty")
    starts = list(occurrences(text, change.text))
    match change.occurrence:
        case "all":
            chosen = starts
        case None:
            chosen = starts if len(starts) == 1 else []
        case n:
            chosen = starts[n - 1 : n] if n >= 1 else []
    if not chosen:
        raise ChangeError(index, _failure(change, len(starts)))
    return [Edit(index, start, change.text, change.replacement) for start in chosen]


def _failure(change: Change, count: int) -> str:
    shown = change.text if len(change.text) <= 60 else change.text[:60] + "..."
    needle = json.dumps(shown, ensure_ascii=False)
    if count == 0:
        return f"{needle} does not appear in the document"
    times = "once" if count == 1 else f"{count} times"
    if change.occurrence is None:
        return f"{needle} appears {times} and no occurrence was given"
    return f"{needle} appears {times}, so there is no occurrence {change.occurrence}"


def ordered(edits: Iterable[Edit]) -> list[Edit]:
    """The edits in document order, or ChangeError when two of them overlap."""
    result = sorted(edits, key=lambda edit: (edit.start, edit.end))
    for a, b in pairwise(result):
        if b.start < a.end:
            later, earlier = max(a.change, b.change), min(a.change, b.change)
            raise ChangeError(later, f"overlaps changes[{earlier}] at offset {b.start}")
    return result


def apply_changes(text: str, changes: Sequence[Change | RangeChange]) -> tuple[str, list[Edit]]:
    """Apply every change, or none. Returns the new text and the edits in document order."""
    edits = ordered(
        edit for index, change in enumerate(changes) for edit in resolve(text, change, index)
    )
    parts: list[str] = []
    cursor = 0
    for edit in edits:
        parts += (text[cursor : edit.start], edit.inserted)
        cursor = edit.end
    parts.append(text[cursor:])
    return "".join(parts), edits
