import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import pytest

from redline.changes import Change, RangeChange
from redline.service import Service, VersionConflict, VersionRequired
from redline.store import NotFound

TEXT = "Payment is due Net 30 after receipt of an invoice."
WRITERS = 16


def test_a_dry_run_stores_nothing() -> None:
    service = Service()
    document = service.create("Terms", TEXT)
    result = service.edit(document.id, [Change("Net 30", "Net 45")], dry_run=True)
    assert result.text == TEXT.replace("Net 30", "Net 45")
    assert not result.committed
    assert result.document == document
    assert service.get(document.id) == document


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_stale_if_match_is_a_conflict(dry_run: bool) -> None:
    service = Service()
    document = service.create("Terms", TEXT)
    service.edit(document.id, [Change("Payment", "Settlement")])
    message = "document is at version 2, not 1; fetch it again and retry"
    with pytest.raises(VersionConflict, match=f"^{message}$"):
        service.edit(document.id, [Change("Net 30", "Net 45")], if_match=1, dry_run=dry_run)
    assert service.get(document.id).version == 2


@pytest.mark.parametrize("dry_run", [False, True])
def test_a_range_change_needs_if_match(dry_run: bool) -> None:
    service = Service()
    document = service.create("Terms", TEXT)
    message = (
        "a range change needs If-Match: "
        "an offset only means something in one version of the document"
    )
    with pytest.raises(VersionRequired, match=f"^{message}$"):
        service.edit(document.id, [RangeChange(0, 7, "Settlement")], dry_run=dry_run)
    assert service.get(document.id) == document


def test_a_range_change_with_the_current_version_applies() -> None:
    service = Service()
    document = service.create("Terms", TEXT)
    result = service.edit(document.id, [RangeChange(0, 7, "Settlement")], if_match=1)
    assert result.document.text == TEXT.replace("Payment", "Settlement")
    assert result.document.version == 2


def test_a_range_change_with_a_stale_version_is_a_conflict() -> None:
    service = Service()
    document = service.create("Terms", TEXT)
    service.edit(document.id, [Change("Net 30", "Net 45")])
    with pytest.raises(VersionConflict):
        service.edit(document.id, [RangeChange(0, 7, "Settlement")], if_match=1)


def test_an_unknown_document_is_not_found_even_for_a_range_change_without_a_version() -> None:
    with pytest.raises(NotFound, match="^document nope not found$"):
        Service().edit("nope", [RangeChange(0, 1, "x")])


def finishes_while_locked(lock: threading.Lock, run: Callable[[], object]) -> bool:
    """Whether run() returns while this thread holds lock."""
    with lock:
        worker = threading.Thread(target=run)
        worker.start()
        worker.join(timeout=5)
        finished = not worker.is_alive()
    worker.join()
    return finished


def test_a_preview_does_not_wait_for_the_documents_lock() -> None:
    service = Service()
    document = service.create("Terms", TEXT)
    preview = partial(service.edit, document.id, [Change("Net 30", "Net 45")], dry_run=True)
    assert finishes_while_locked(service._store.lock(document.id), preview)


def test_a_commit_does_not_wait_for_another_documents_lock() -> None:
    service = Service()
    locked, other = service.create("Locked", TEXT), service.create("Other", TEXT)
    commit = partial(service.edit, other.id, [Change("Net 30", "Net 45")])
    assert finishes_while_locked(service._store.lock(locked.id), commit)
    assert service.get(other.id).version == 2


def test_search_sees_the_text_after_an_edit() -> None:
    service = Service()
    document = service.create("Terms", TEXT)
    assert service.search("net 30")[0] == 1
    service.edit(document.id, [Change("Net 30", "Net 45")])
    assert service.search("net 30")[0] == 0
    assert service.search("net 45")[0] == 1


def test_search_can_be_scoped_to_one_document() -> None:
    service = Service()
    first, second = service.create("First", TEXT), service.create("Second", TEXT)
    assert {found.id for found, _ in service.search("invoice")[1]} == {first.id, second.id}
    assert [found.id for found, _ in service.search("invoice", second.id)[1]] == [second.id]
    with pytest.raises(NotFound):
        service.search("invoice", "nope")


def test_search_returns_the_first_ten_hits_paired_with_their_document() -> None:
    service = Service()
    clauses = "\n\n".join(f"clause {n} shall apply" for n in range(12))
    document = service.create("Clauses", clauses)
    total, hits = service.search("shall")
    assert total == 12
    starts = [22 * n for n in range(10)]
    assert [(found, hit.block.start) for found, hit in hits] == [(document, s) for s in starts]


def edit_concurrently(service: Service, doc_id: str, if_match: int | None) -> list[str]:
    """Every writer replaces its own marker "<n>" at the same moment; returns each outcome."""
    start = threading.Barrier(WRITERS)

    def write(n: int) -> str:
        start.wait()
        try:
            service.edit(doc_id, [Change(f"<{n}>", f"[{n}]")], if_match=if_match)
        except VersionConflict:
            return "conflict"
        return "committed"

    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # switch threads often, so that a race has a chance to show
    try:
        with ThreadPoolExecutor(WRITERS) as pool:
            return list(pool.map(write, range(WRITERS)))
    finally:
        sys.setswitchinterval(interval)


def test_of_concurrent_edits_with_the_same_if_match_exactly_one_commits() -> None:
    service = Service()
    document = service.create("Markers", " ".join(f"<{n}>" for n in range(WRITERS)))
    outcomes = edit_concurrently(service, document.id, if_match=1)
    assert outcomes.count("committed") == 1
    assert outcomes.count("conflict") == WRITERS - 1
    assert service.get(document.id).version == 2


def test_concurrent_edits_to_different_text_all_commit() -> None:
    service = Service()
    document = service.create("Markers", " ".join(f"<{n}>" for n in range(WRITERS)))
    outcomes = edit_concurrently(service, document.id, if_match=None)
    assert outcomes == ["committed"] * WRITERS
    final = service.get(document.id)
    assert final.text == " ".join(f"[{n}]" for n in range(WRITERS))
    assert final.version == 1 + WRITERS
