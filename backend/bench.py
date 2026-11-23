"""Print the benchmark tables: synthetic contracts of 1 MB and 10 MB, then 10,000 of 20 KB."""

import resource
import sys
import time
from collections.abc import Callable

from redline.changes import Change, apply_changes
from redline.search import WORD, Index, blocks, search, terms
from redline.service import Service
from redline.synthetic import contract

MB = 1_000_000
DOCUMENTS, DOCUMENT_SIZE = 10_000, 20_000  # 20 KB each
COMMON_QUERY = "agreement shall"
SELECTIVE_QUERY = "larkspur tamberlane delaware"
TERMS = ["invoice", "receipt", "Neither", "indirect", "consequential", "damages", "twelve"]
TERMS += ["months", "governed", "jurisdiction", "Confidential", "secret", "perform", "indemnify"]
TERMS += ["third-party", "breach", "terminate", "written", "assign", "consent"]
TWENTY_TERMS = [Change(term, term.upper(), "all") for term in TERMS]


def best[T](run: Callable[[], T]) -> tuple[float, T]:
    """The fastest of three runs, in seconds, and the result of the last one."""
    times = []
    for _ in range(3):
        start = time.perf_counter()
        result = run()
        times.append(time.perf_counter() - start)
    return min(times), result


def cell(run: Callable[[], object], counted: str = "") -> str:
    seconds, result = best(run)
    return f"{seconds * 1000:,.1f} ms" + (f" ({result:,} {counted})" if counted else "")


def replace_all(text: str) -> int:
    """Replace every "Agreement"; returns the number of edits."""
    return len(apply_changes(text, [Change("Agreement", "Contract", "all")])[1])


def scan(text: str, query: str) -> int:
    """Count the blocks holding every query term by reading each one: no index."""
    wanted = set(terms(query))
    return sum(wanted <= {w.casefold() for w in WORD.findall(text, *b)} for b in blocks(text))


def column(text: str) -> dict[str, str]:
    """Each operation's cell for this text."""
    last_clause = text[text.rindex("\n") + 1 :]
    index = Index(text)
    return {
        "one unique replace": cell(
            lambda: apply_changes(text, [Change(last_clause, last_clause.upper())])
        ),
        'replace every "Agreement"': cell(lambda: replace_all(text), "matches"),
        "change set of 20 distinct terms": cell(lambda: apply_changes(text, TWENTY_TERMS)),
        "build the index": cell(lambda: Index(text)),
        f'search "{COMMON_QUERY}"': cell(lambda: search({"": index}, COMMON_QUERY)[0], "hits"),
        f'search "{SELECTIVE_QUERY}"': cell(
            lambda: search({"": index}, SELECTIVE_QUERY)[0], "hits"
        ),
        f'scan every block for "{COMMON_QUERY}" (no index)': cell(
            lambda: scan(text, COMMON_QUERY), "hits"
        ),
    }


def many_documents() -> dict[str, str]:
    """Rows for 10,000 small contracts held by one service and searched together."""
    service = Service()
    for seed in range(DOCUMENTS):
        service.create(f"Contract {seed}", contract(DOCUMENT_SIZE, seed))
    start = time.perf_counter()
    service.search(SELECTIVE_QUERY)
    first = time.perf_counter() - start
    return {
        "first search, which builds every index": f"{first:.1f} s",
        f'search "{SELECTIVE_QUERY}"': cell(lambda: service.search(SELECTIVE_QUERY)[0], "hits"),
        f'search "{COMMON_QUERY}"': cell(lambda: service.search(COMMON_QUERY)[0], "hits"),
        "peak memory of this process": f"{peak_memory_mb():,.0f} MB",
    }


def peak_memory_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # bytes on macOS, KB on Linux
    return peak / 1e6 if sys.platform == "darwin" else peak / 1e3


def main() -> None:
    small, large = column(contract(MB)), column(contract(10 * MB))
    print("| operation | 1 MB | 10 MB |\n|---|---|---|")
    for operation in small:
        print(f"| {operation} | {small[operation]} | {large[operation]} |")
    print("\n| 10,000 documents of 20 KB, searched together | |\n|---|---|")
    for operation, value in many_documents().items():
        print(f"| {operation} | {value} |")


if __name__ == "__main__":
    main()
