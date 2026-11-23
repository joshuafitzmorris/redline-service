import pytest

from redline.changes import Change, ChangeError, Edit, RangeChange, apply_changes

TERMS = "Payment is Net 30. Late invoices are Net 30 plus interest. Refunds are Net 30."


def test_a_unique_match_is_replaced() -> None:
    text, _ = apply_changes(TERMS, [Change("Late invoices", "Overdue invoices")])
    assert text == TERMS.replace("Late invoices", "Overdue invoices")


def test_an_occurrence_picks_the_nth_match() -> None:
    text, edits = apply_changes(TERMS, [Change("Net 30", "Net 45", occurrence=2)])
    assert text == "Payment is Net 30. Late invoices are Net 45 plus interest. Refunds are Net 30."
    assert [edit.start for edit in edits] == [TERMS.index("Net 30 plus")]


def test_all_replaces_every_match() -> None:
    text, edits = apply_changes(TERMS, [Change("Net 30", "Net 45", occurrence="all")])
    assert text == TERMS.replace("Net 30", "Net 45")
    assert len(edits) == 3


def test_an_empty_replacement_deletes() -> None:
    text, _ = apply_changes(TERMS, [Change(" plus interest", "")])
    assert text == "Payment is Net 30. Late invoices are Net 30. Refunds are Net 30."


def test_text_that_does_not_appear_is_an_error() -> None:
    with pytest.raises(ChangeError) as error:
        apply_changes(TERMS, [Change("Net 60", "Net 45")])
    assert str(error.value) == 'changes[0]: "Net 60" does not appear in the document'


def test_an_ambiguous_match_is_an_error_that_gives_the_count() -> None:
    with pytest.raises(ChangeError) as error:
        apply_changes(TERMS, [Change("Net 30", "Net 45")])
    assert error.value.reason == '"Net 30" appears 3 times and no occurrence was given'


def test_an_occurrence_past_the_last_match_is_an_error() -> None:
    with pytest.raises(ChangeError) as error:
        apply_changes(TERMS, [Change("Net 30", "Net 45", occurrence=5)])
    assert error.value.reason == '"Net 30" appears 3 times, so there is no occurrence 5'


@pytest.mark.parametrize("occurrence", [0, -1, 10**30])
def test_an_occurrence_that_cannot_exist_is_an_error(occurrence: int) -> None:
    with pytest.raises(ChangeError) as error:
        apply_changes(TERMS, [Change("Net 30", "Net 45", occurrence)])
    assert error.value.reason == f'"Net 30" appears 3 times, so there is no occurrence {occurrence}'


def test_an_occurrence_past_a_single_match_says_once() -> None:
    with pytest.raises(ChangeError) as error:
        apply_changes(TERMS, [Change("Payment", "Settlement", occurrence=2)])
    assert error.value.reason == '"Payment" appears once, so there is no occurrence 2'


def test_empty_text_to_replace_is_an_error() -> None:
    with pytest.raises(ChangeError) as error:
        apply_changes(TERMS, [Change("", "Net 45")])
    assert error.value.reason == "the text to replace is empty"


def test_the_empty_text_error_names_its_change() -> None:
    with pytest.raises(ChangeError) as error:
        apply_changes(TERMS, [Change("Payment", "Settlement"), Change("", "Net 45")])
    assert str(error.value) == "changes[1]: the text to replace is empty"


@pytest.mark.parametrize(
    ("missing", "shown"),
    [
        ("x" * 60, '"' + "x" * 60 + '"'),
        ("x" * 61, '"' + "x" * 60 + '..."'),
        ("\N{LEFT DOUBLE QUOTATION MARK}Net 60", '"\N{LEFT DOUBLE QUOTATION MARK}Net 60"'),
    ],
)
def test_an_error_quotes_the_text_as_json_cut_at_60_characters(missing: str, shown: str) -> None:
    with pytest.raises(ChangeError) as error:
        apply_changes(TERMS, [Change(missing, "Net 45")])
    assert error.value.reason == f"{shown} does not appear in the document"


def test_changes_resolve_against_the_base_text_not_each_other() -> None:
    text, _ = apply_changes("A B", [Change("A", "B"), Change("B", "C")])
    assert text == "B C"


def test_the_order_of_changes_does_not_matter() -> None:
    first, second = Change("Payment", "Settlement"), Change("Refunds", "Credits")
    assert apply_changes(TERMS, [first, second])[0] == apply_changes(TERMS, [second, first])[0]


def test_overlapping_changes_are_rejected_and_both_are_named() -> None:
    with pytest.raises(ChangeError) as error:
        apply_changes(TERMS, [Change("Late invoices", "x"), Change("invoices are", "y")])
    assert error.value.change == 1
    assert error.value.reason == f"overlaps changes[0] at offset {TERMS.index('invoices are')}"


def test_an_overlap_names_the_later_change_even_when_it_comes_first_in_the_text() -> None:
    with pytest.raises(ChangeError) as error:
        apply_changes(TERMS, [Change("invoices are", "y"), Change("Late invoices", "x")])
    assert error.value.change == 1
    assert error.value.reason == f"overlaps changes[0] at offset {TERMS.index('invoices are')}"


def test_a_failing_change_applies_nothing() -> None:
    changes = [Change("Payment", "Settlement"), Change("Net 60", "Net 45")]
    with pytest.raises(ChangeError) as error:
        apply_changes(TERMS, changes)
    assert error.value.change == 1


def test_matches_do_not_overlap() -> None:
    text, edits = apply_changes("aaaa", [Change("aa", "b", occurrence="all")])
    assert text == "bb"
    assert [edit.start for edit in edits] == [0, 2]


def test_offsets_count_code_points() -> None:
    _, edits = apply_changes("\N{PAGE FACING UP} Net 30", [Change("Net 30", "Net 45")])
    assert edits[0].start == 2


def test_edits_come_back_in_document_order() -> None:
    changes = [Change("Refunds", "Credits"), Change("Payment", "Settlement")]
    _, edits = apply_changes(TERMS, changes)
    assert edits == [
        Edit(change=1, start=0, removed="Payment", inserted="Settlement"),
        Edit(change=0, start=TERMS.index("Refunds"), removed="Refunds", inserted="Credits"),
    ]


def test_a_range_replaces_exactly_its_span() -> None:
    start = TERMS.index("Late")
    text, edits = apply_changes(TERMS, [RangeChange(start, start + 4, "Overdue")])
    assert text == TERMS.replace("Late", "Overdue")
    assert edits == [Edit(change=0, start=start, removed="Late", inserted="Overdue")]


def test_an_empty_range_inserts() -> None:
    text, edits = apply_changes(TERMS, [RangeChange(7, 7, " in full")])
    assert text == TERMS.replace("Payment", "Payment in full")
    assert edits == [Edit(change=0, start=7, removed="", inserted=" in full")]


def test_a_range_may_reach_either_end_of_the_document() -> None:
    assert apply_changes("abc", [RangeChange(0, 3, "x")])[0] == "x"
    assert apply_changes("abc", [RangeChange(3, 3, "d"), RangeChange(0, 0, "_")])[0] == "_abcd"


@pytest.mark.parametrize(("start", "end"), [(0, len(TERMS) + 1), (5, 4), (-1, 3)])
def test_a_range_outside_the_document_is_an_error(start: int, end: int) -> None:
    with pytest.raises(ChangeError) as error:
        apply_changes(TERMS, [RangeChange(start, end, "x")])
    assert str(error.value) == (
        f"changes[0]: {start} to {end} is not a range within the document (0 to {len(TERMS)})"
    )


def test_text_and_range_changes_both_resolve_against_the_base_text() -> None:
    text, _ = apply_changes("A B", [Change("A", "AAA"), RangeChange(2, 3, "C")])
    assert text == "AAA C"


def test_two_insertions_at_the_same_offset_are_both_applied_in_request_order() -> None:
    assert apply_changes("ab", [RangeChange(1, 1, "1"), RangeChange(1, 1, "2")])[0] == "a12b"
    assert apply_changes("ab", [RangeChange(1, 1, "2"), RangeChange(1, 1, "1")])[0] == "a21b"


def test_an_insertion_at_either_end_of_a_replaced_span_is_applied() -> None:
    replace_b = Change("b", "B")
    assert apply_changes("abc", [replace_b, RangeChange(1, 1, "<")])[0] == "a<Bc"
    assert apply_changes("abc", [RangeChange(1, 1, "<"), replace_b])[0] == "a<Bc"
    assert apply_changes("abc", [replace_b, RangeChange(2, 2, ">")])[0] == "aB>c"
    assert apply_changes("abc", [RangeChange(2, 2, ">"), replace_b])[0] == "aB>c"


def test_an_insertion_strictly_inside_a_replaced_span_overlaps_it() -> None:
    with pytest.raises(ChangeError) as error:
        apply_changes("abcd", [Change("bc", "X"), RangeChange(2, 2, "+")])
    assert str(error.value) == "changes[1]: overlaps changes[0] at offset 2"


def test_two_changes_with_the_same_span_overlap() -> None:
    with pytest.raises(ChangeError) as error:
        apply_changes(TERMS, [Change("Payment", "Fee"), RangeChange(0, 7, "Price")])
    assert str(error.value) == "changes[1]: overlaps changes[0] at offset 0"
