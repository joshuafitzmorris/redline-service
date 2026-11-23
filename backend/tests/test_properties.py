import re

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from redline.changes import Change, ChangeError, RangeChange, apply_changes
from redline.search import WORD, Index, blocks, search, terms

# A small alphabet makes repeated, adjacent and overlapping matches common.
texts = st.text(alphabet="ab \n", max_size=40)
needles = st.text(alphabet="ab \n", min_size=1, max_size=3)
replacements = st.text(alphabet="abc", max_size=3)
occurrences = st.none() | st.just("all") | st.integers(min_value=-1, max_value=4)
offsets = st.integers(min_value=-1, max_value=12)
changes = st.builds(Change, needles, replacements, occurrences) | st.builds(
    RangeChange, offsets, offsets, replacements
)


def naive(text: str, changes: list[Change | RangeChange]) -> str | None:
    """Regex matching and right-to-left splicing; None when the change set should be rejected."""
    spans: list[tuple[int, int, int, str]] = []  # start, end, index in the request, replacement
    for index, change in enumerate(changes):
        if isinstance(change, RangeChange):
            if not (0 <= change.start <= change.end <= len(text)):
                return None
            spans.append((change.start, change.end, index, change.replacement))
            continue
        starts = [match.start() for match in re.finditer(re.escape(change.text), text)]
        if change.occurrence == "all":
            chosen = starts
        elif change.occurrence is None:
            chosen = starts if len(starts) == 1 else []
        elif 1 <= change.occurrence <= len(starts):
            chosen = [starts[change.occurrence - 1]]
        else:
            chosen = []
        if not chosen:
            return None
        spans += [(start, start + len(change.text), index, change.replacement) for start in chosen]
    covered = [position for start, end, _, _ in spans for position in range(start, end)]
    if len(covered) != len(set(covered)):
        return None
    insertions = [start for start, end, _, _ in spans if start == end]
    if any(start < point < end for point in insertions for start, end, _, _ in spans):
        return None
    for start, end, _, replacement in sorted(spans, reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


@given(texts, needles, replacements)
def test_replacing_every_occurrence_equals_str_replace(
    text: str, needle: str, replacement: str
) -> None:
    assume(needle in text)
    new_text, _ = apply_changes(text, [Change(needle, replacement, "all")])
    assert new_text == text.replace(needle, replacement)


@given(texts, st.lists(changes, min_size=1, max_size=4))
def test_a_change_set_matches_a_naive_oracle(
    text: str, change_set: list[Change | RangeChange]
) -> None:
    expected = naive(text, change_set)
    if expected is None:
        with pytest.raises(ChangeError):
            apply_changes(text, change_set)
    else:
        new_text, edits = apply_changes(text, change_set)
        assert new_text == expected
        assert all(text[edit.start : edit.end] == edit.removed for edit in edits)


@given(st.text(alphabet="abAB \n", max_size=200), st.text(alphabet="abAB ", max_size=8))
def test_indexed_search_finds_exactly_what_a_scan_finds(text: str, query: str) -> None:
    wanted = set(terms(query))
    expected = {
        block
        for block in blocks(text)
        if wanted and wanted <= {word.casefold() for word in WORD.findall(text, *block)}
    }
    total, hits = search({"doc": Index(text)}, query, limit=1_000)
    assert total == len(expected)
    assert {hit.block for hit in hits} == expected
    assert all(text[start:end].casefold() in wanted for hit in hits for start, end in hit.matches)
