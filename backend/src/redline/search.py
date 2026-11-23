"""Search: split a document into clause-sized blocks, index their words, rank blocks with BM25."""

import heapq
import math
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from typing import NamedTuple

WORD = re.compile(r"[^\W_]+")  # letters and digits; \w would also take the underscore
GAP = re.compile(r"\n\s*\n")  # one or more blank lines
MAX_BLOCK = 1_000  # characters
K1, B = 1.2, 0.75  # BM25 constants (Lucene's defaults)


class Span(NamedTuple):
    start: int
    end: int


def terms(query: str) -> list[str]:
    """The words of a query, casefolded, in order, without repeats."""
    return list(dict.fromkeys(word.casefold() for word in WORD.findall(query)))


def blocks(text: str) -> list[Span]:
    """Clause-sized pieces of text: paragraphs, a long one cut at a line break, space or tab."""
    edges = [0, *(gap.start() for gap in GAP.finditer(text)), len(text)]
    pieces: list[Span] = []
    for start, end in pairwise(edges):
        while end - start > MAX_BLOCK:
            cut, next_start = cut_point(text, start)
            pieces.append(Span(start, cut))
            start = next_start
        pieces.append(Span(start, end))
    trimmed = (trim(text, piece) for piece in pieces)
    return [piece for piece in trimmed if piece.start < piece.end]


def cut_point(text: str, start: int) -> tuple[int, int]:
    """Where a too-long block starting at `start` ends, and where the next one begins."""
    limit = start + MAX_BLOCK
    for separator in "\n", " ", "\t":
        at = text.rfind(separator, start + 1, limit + 1)
        if at != -1:
            return at, at + 1
    return limit, limit


def trim(text: str, span: Span) -> Span:
    """The span without the whitespace at its ends. A span of only whitespace comes back empty."""
    piece = text[span.start : span.end]
    return Span(span.end - len(piece.lstrip()), span.start + len(piece.rstrip()))


class Index:
    """Inverted index of one document: term -> {block number: occurrences in that block}."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.blocks = blocks(text)
        self.sizes: list[int] = []
        self.postings: dict[str, dict[int, int]] = {}
        for number, (start, end) in enumerate(self.blocks):
            counts = Counter(word.casefold() for word in WORD.findall(text, start, end))
            self.sizes.append(counts.total())
            for term, count in counts.items():
                self.postings.setdefault(term, {})[number] = count
        self.words = sum(self.sizes)

    def candidates(self, query_terms: list[str]) -> list[int]:
        """Numbers of the blocks that contain every term, in document order."""
        if any(term not in self.postings for term in query_terms):
            return []
        smallest, *others = sorted((self.postings[term] for term in query_terms), key=len)
        return [number for number in smallest if all(number in other for other in others)]


@dataclass(frozen=True, slots=True)
class Hit:
    key: str  # which index (document id) the block belongs to
    block: Span
    score: float
    matches: list[Span]  # where the query's words are, as document offsets


def search(
    indexes: Mapping[str, Index], query: str, *, limit: int = 10, offset: int = 0
) -> tuple[int, list[Hit]]:
    """Blocks that contain every query term, best first. Returns (total hits, requested page)."""
    query_terms = terms(query)
    total_blocks = sum(len(index.blocks) for index in indexes.values())
    if not query_terms or total_blocks == 0:
        return 0, []
    average = sum(index.words for index in indexes.values()) / total_blocks
    idf = {}
    for term in query_terms:
        df = sum(len(index.postings.get(term, ())) for index in indexes.values())
        idf[term] = math.log(1 + (total_blocks - df + 0.5) / (df + 0.5))

    ranked: list[tuple[float, str, int]] = []
    for key, index in indexes.items():
        for number in index.candidates(query_terms):
            norm = 1 - B + B * index.sizes[number] / average
            score = 0.0
            for term in query_terms:
                tf = index.postings[term][number]
                score += idf[term] * tf * (K1 + 1) / (tf + K1 * norm)
            ranked.append((-score, key, number))

    hits = []
    for negative_score, key, number in heapq.nsmallest(offset + limit, ranked)[offset:]:
        text, block = indexes[key].text, indexes[key].blocks[number]
        words = WORD.finditer(text, block.start, block.end)
        matches = [Span(*word.span()) for word in words if word.group().casefold() in query_terms]
        hits.append(Hit(key, block, -negative_score, matches))
    return len(ranked), hits
