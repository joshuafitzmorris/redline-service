import pytest

from redline.search import MAX_BLOCK, Index, Span, blocks, search, terms

CONTRACT = """\
1. Payment. Licensee shall pay within Net 30 days.

2. Liability for indemnified claims is not limited.
   \t
3. Limitation of Liability. Liability is capped at the fees paid.

4. Governing Law. This Agreement is governed by the laws of Delaware.
"""

WRAPPED = """\
9. Limitation of Liability. Neither party is liable for indirect,
incidental or consequential damages, or for lost profits, even if
advised of their possibility.

10. Indemnification. Licensor shall defend Licensee against any claim.
"""


def find(text: str, query: str, **page: int) -> tuple[int, list[Span]]:
    total, hits = search({"doc": Index(text)}, query, **page)
    return total, [hit.block for hit in hits]


def snippets(text: str, query: str) -> list[str]:
    return [text[block.start : block.end] for block in find(text, query)[1]]


def pieces(text: str) -> list[str]:
    return [text[start:end] for start, end in blocks(text)]


def test_blocks_are_paragraphs_separated_by_blank_lines() -> None:
    assert pieces(CONTRACT) == [line for line in CONTRACT.splitlines() if line.strip()]
    assert pieces("a\r\nb\r\n\r\nc\r\n") == ["a\r\nb", "c"]


def test_a_hard_wrapped_paragraph_is_one_block() -> None:
    assert pieces(WRAPPED) == WRAPPED.strip().split("\n\n")
    assert snippets(WRAPPED, "indirect consequential possibility") == [pieces(WRAPPED)[0]]


def test_blocks_are_trimmed() -> None:
    assert pieces("  \n\t first clause \n\n  second clause  ") == ["first clause", "second clause"]


def test_a_long_paragraph_is_cut_at_a_line_break_rather_than_a_space() -> None:
    text = " ".join(["term"] * 120) + "\n" + " ".join(["rule"] * 120)
    assert len(text) > MAX_BLOCK
    assert pieces(text) == text.split("\n")


def test_text_without_blank_lines_is_cut_between_lines() -> None:
    lines = [f"clause {n:02} " + "x" * 70 for n in range(20)]
    text = "\n".join(lines)
    assert pieces(text) == ["\n".join(lines[:12]), "\n".join(lines[12:])]


def test_a_long_line_is_split_at_a_space_and_no_block_is_longer_than_the_limit() -> None:
    line = " ".join(["indemnify"] * 500)
    split = pieces(line)
    assert all(0 < len(piece) <= MAX_BLOCK for piece in split)
    assert all(piece.startswith("indemnify") and piece.endswith("indemnify") for piece in split)
    assert " ".join(split) == line


def test_a_long_line_is_split_at_the_last_space_within_the_limit() -> None:
    words = " ".join(["indemnify"] * 500)
    assert blocks(words) == [Span(n, n + 999) for n in range(0, 5_000, 1_000)]
    assert blocks("x" * 1_001 + " y") == [Span(0, 1_000), Span(1_000, 1_003)]
    assert blocks("a " + "x" * 1_500) == [Span(0, 1), Span(2, 1_002), Span(1_002, 1_502)]


def test_a_line_of_exactly_the_limit_is_one_block_even_after_a_cut() -> None:
    line = "a " * 499 + "ab"
    assert len(line) == MAX_BLOCK
    assert blocks(line) == [Span(0, MAX_BLOCK)]
    assert blocks("first\n" + line) == [Span(0, 5), Span(6, 1_006)]


def test_a_separator_exactly_at_the_limit_is_a_cut_point() -> None:
    text = "x" * 994 + " " + "y" * 5 + "\n" + "z" * 10
    assert text.index("\n") == MAX_BLOCK
    assert blocks(text) == [Span(0, 1_000), Span(1_001, 1_011)]


def test_a_line_without_spaces_is_cut_at_the_limit() -> None:
    assert blocks("x" * 2_500) == [Span(0, 1_000), Span(1_000, 2_000), Span(2_000, 2_500)]


def test_query_terms_are_casefolded_and_deduplicated() -> None:
    assert terms("Liability, LIABILITY and liability caps") == ["liability", "and", "caps"]


def test_an_underscore_separates_words_and_a_signature_blank_is_not_a_word() -> None:
    text = "Effective_Date: 1 March 2026"
    assert snippets(text, "effective") == snippets(text, "date") == [text]
    assert Index("Signature: " + "_" * 30).postings.keys() == {"signature"}


def test_every_term_must_appear_in_the_same_block() -> None:
    assert snippets(CONTRACT, "liability delaware") == []
    assert snippets(CONTRACT, "governed delaware") == [
        "4. Governing Law. This Agreement is governed by the laws of Delaware."
    ]


def test_matching_ignores_case() -> None:
    assert find(CONTRACT, "LICENSEE") == find(CONTRACT, "licensee")
    assert find(CONTRACT, "licensee")[0] == 1


def test_more_mentions_of_a_term_outrank_an_earlier_block_with_fewer() -> None:
    assert [snippet[:2] for snippet in snippets(CONTRACT, "liability")] == ["3.", "2."]


def test_scores_are_bm25_with_statistics_from_every_searched_document() -> None:
    # 3 blocks of 10/3 words on average; both terms are in 2 of them, so each idf is ln(1.6).
    indexes = {
        "a": Index("late late payment fees apply"),
        "b": Index("late fees\n\ninterest accrues monthly"),
    }
    total, hits = search(indexes, "late fees")
    assert total == 2
    assert [hit.key for hit in hits] == ["b", "a"]
    assert [hit.score for hit in hits] == pytest.approx([1.123922, 0.956771], abs=1e-6)


def test_equal_scores_are_ordered_by_document_key_then_position() -> None:
    indexes = {"b": Index("net 30\n\nnet 30"), "a": Index("other\n\nnet 30")}
    _, hits = search(indexes, "net")
    assert [(hit.key, hit.block.start) for hit in hits] == [("a", 7), ("b", 0), ("b", 8)]


def test_matches_point_at_the_query_words() -> None:
    _, hits = search({"doc": Index(CONTRACT)}, "liability limitation")
    words = [CONTRACT[start:end] for start, end in hits[0].matches]
    assert words == ["Limitation", "Liability", "Liability"]


def test_page_slices_line_up_with_the_text() -> None:
    _, hits = search({"doc": Index(CONTRACT)}, "net 30")
    assert CONTRACT[hits[0].block.start : hits[0].block.end] == CONTRACT.splitlines()[0]


def test_total_limit_and_offset_page_through_a_stable_order() -> None:
    text = "\n\n".join(f"clause {n} shall apply" for n in range(25))
    total, everything = find(text, "shall", limit=100)
    pages = [find(text, "shall", limit=10, offset=offset)[1] for offset in (0, 10, 20)]
    assert total == 25
    assert [block for page in pages for block in page] == everything
    assert [len(page) for page in pages] == [10, 10, 5]
    assert find(text, "shall")[1] == everything[:10]


def test_several_documents_are_ranked_together() -> None:
    indexes = {"nda": Index("Confidential Information stays confidential."), "msa": Index(CONTRACT)}
    total, hits = search(indexes, "confidential")
    assert total == 1
    assert hits[0].key == "nda"
    assert {hit.key for hit in search(indexes, "the")[1]} == {"msa"}


def test_a_query_without_words_finds_nothing() -> None:
    assert search({"doc": Index(CONTRACT)}, " ,.; ") == (0, [])
    assert search({}, "liability") == (0, [])


def test_blank_lines_at_the_start_make_no_empty_block() -> None:
    assert blocks("") == []
    assert blocks("\n\nclause") == [Span(2, 8)]


def test_matches_stay_inside_their_block() -> None:
    _, hits = search({"doc": Index("late fee\n\nlate payment")}, "late fee")
    assert [(hit.block, hit.matches) for hit in hits] == [(Span(0, 8), [Span(0, 4), Span(5, 8)])]
