import timeit
from collections.abc import Callable

import pytest

from redline.changes import Change, apply_changes, occurrences
from redline.search import Index, blocks, search
from redline.synthetic import contract

MB = 1_000_000


@pytest.fixture(scope="module")
def ten_mb() -> str:
    return contract(10 * MB)


def best_of_three(run: Callable[[], object]) -> float:
    return min(timeit.repeat(run, number=1, repeat=3))


def growth(operation: Callable[[str], object], make: Callable[[int], str] = contract) -> float:
    """How many times longer the operation takes on 8 MB than on 1 MB."""
    small, large = make(MB), make(8 * MB)
    return best_of_three(lambda: operation(large)) / best_of_three(lambda: operation(small))


def test_a_bulk_change_set_on_10_mb_is_correct(ten_mb: str) -> None:
    changes = [
        Change("Net 30", "Net 90", "all"),
        Change("Delaware", "Maryland", "all"),
        Change("Master Services Agreement", "Services Agreement"),
    ]
    text, edits = apply_changes(ten_mb, changes)
    expected = ten_mb.replace("Net 30", "Net 90").replace("Delaware", "Maryland")
    assert text == expected.replace("Master Services Agreement", "Services Agreement")
    assert len(edits) == ten_mb.count("Net 30") + ten_mb.count("Delaware") + 1


def test_a_search_on_10_mb_is_correct(ten_mb: str) -> None:
    total, hits = search({"big": Index(ten_mb)}, "governing delaware", limit=100)
    assert total == ten_mb.count("State of Delaware")
    assert all("Delaware" in ten_mb[hit.block.start : hit.block.end] for hit in hits)


def test_replace_all_time_grows_linearly() -> None:
    change = [Change("Agreement", "Contract", "all")]
    assert growth(lambda text: apply_changes(text, change)) < 16


def test_finding_text_grows_linearly_with_an_adversarial_needle() -> None:
    needle = "a" * 999 + "b"
    assert growth(lambda text: list(occurrences(text, needle)), lambda size: "a" * size) < 16


def test_index_build_time_grows_linearly() -> None:
    assert growth(Index) < 16


@pytest.mark.parametrize(
    "make",
    [
        lambda size: "x" * size,
        lambda size: " " * size,
        lambda size: "\n" * size,
        lambda size: ("\n" + " " * MB + "x") * (size // MB),
    ],
    ids=["no whitespace", "spaces", "newlines", "long runs of spaces between lines"],
)
def test_splitting_awkward_text_into_blocks_grows_linearly(make: Callable[[int], str]) -> None:
    assert growth(blocks, make) < 16
