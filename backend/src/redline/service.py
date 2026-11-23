"""Use cases. `edit` is the only code that writes document text."""

from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, replace

from redline.changes import Change, ChangeError, Edit, RangeChange, apply_changes, ordered, resolve
from redline.search import Hit, Index
from redline.search import search as search_indexes
from redline.store import Document, DocumentStore
from redline.suggest import Proposal

MAX_SUGGEST_CHARS = 200_000

Proposer = Callable[[str, str], list[Proposal]]  # (instruction, document text) -> proposals


class VersionConflict(Exception):
    """The caller expected a version of the document that is no longer current."""


class VersionRequired(Exception):
    """A range change came without the version its offsets refer to."""


class TooLarge(Exception):
    """The document is too large to send to a model."""


class Unavailable(Exception):
    """Suggestions are not configured."""


@dataclass(frozen=True, slots=True)
class EditResult:
    document: Document  # the new version when committed, otherwise the current one
    text: str  # the text after the changes
    edits: list[Edit]
    committed: bool


@dataclass(frozen=True, slots=True)
class Suggestion:
    change: Change
    rationale: str


@dataclass(frozen=True, slots=True)
class Rejected:
    proposal: Proposal
    reason: str


@dataclass(frozen=True, slots=True)
class Suggestions:
    version: int
    accepted: list[Suggestion]
    rejected: list[Rejected]


class Service:
    def __init__(self, propose: Proposer | None = None) -> None:
        self._store = DocumentStore()
        self._propose = propose
        self._indexes: dict[str, tuple[int, Index]] = {}

    def create(self, title: str, text: str) -> Document:
        return self._store.add(title, text)

    def get(self, doc_id: str) -> Document:
        return self._store.get(doc_id)

    def documents(self) -> list[Document]:
        return self._store.all()

    def edit(
        self,
        doc_id: str,
        changes: Sequence[Change | RangeChange],
        *,
        if_match: int | None = None,
        dry_run: bool = False,
    ) -> EditResult:
        """Apply a change set atomically, or preview it with dry_run."""
        # A preview reads one immutable snapshot, so only a commit needs the document's lock.
        with nullcontext() if dry_run else self._store.lock(doc_id):
            document = self._store.get(doc_id)
            if if_match is None and any(isinstance(change, RangeChange) for change in changes):
                raise VersionRequired(
                    "a range change needs If-Match: "
                    "an offset only means something in one version of the document"
                )
            if if_match is not None and if_match != document.version:
                raise VersionConflict(
                    f"document is at version {document.version}, not {if_match}; "
                    "fetch it again and retry"
                )
            text, edits = apply_changes(document.text, changes)
            if not dry_run:
                document = replace(document, version=document.version + 1, text=text)
                self._store.put(document)
        return EditResult(document, text, edits, committed=not dry_run)

    def search(
        self, query: str, doc_id: str | None = None, *, limit: int = 10, offset: int = 0
    ) -> tuple[int, list[tuple[Document, Hit]]]:
        """Rank the blocks of one document, or of every document when no id is given."""
        documents = self._store.all() if doc_id is None else [self._store.get(doc_id)]
        by_id = {document.id: document for document in documents}
        indexes = {document.id: self._index(document) for document in documents}
        total, hits = search_indexes(indexes, query, limit=limit, offset=offset)
        return total, [(by_id[hit.key], hit) for hit in hits]

    def _index(self, document: Document) -> Index:
        # Without a lock two searches may build the same index; that costs time, never correctness.
        cached = self._indexes.get(document.id)
        if cached is None or cached[0] != document.version:
            cached = (document.version, Index(document.text))
            self._indexes[document.id] = cached
        return cached[1]

    def suggest(self, doc_id: str, instruction: str) -> Suggestions:
        """Ask the model for changes and keep those that apply cleanly together. Never writes."""
        document = self._store.get(doc_id)
        if self._propose is None:
            raise Unavailable("suggestions need a model: set ANTHROPIC_API_KEY and restart")
        if len(document.text) > MAX_SUGGEST_CHARS:
            raise TooLarge(
                f"document has {len(document.text):,} characters; "
                f"suggestions work on up to {MAX_SUGGEST_CHARS:,}"
            )
        accepted: list[Suggestion] = []
        rejected: list[Rejected] = []
        edits: list[Edit] = []
        for proposal in self._propose(instruction, document.text):
            candidate = Change(proposal.quote, proposal.replacement)
            # The resolution and overlap check apply_changes runs, one proposal at a time; the
            # candidate's index is its place in `accepted`, so a reason names a returned suggestion.
            try:
                edits = ordered([*edits, *resolve(document.text, candidate, len(accepted))])
            except ChangeError as error:
                rejected.append(Rejected(proposal, error.reason))
            else:
                accepted.append(Suggestion(candidate, proposal.rationale))
        return Suggestions(document.version, accepted, rejected)
