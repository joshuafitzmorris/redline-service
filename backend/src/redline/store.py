"""Documents held in memory as immutable snapshots."""

import threading
from dataclasses import dataclass
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class Document:
    id: str
    title: str
    version: int  # starts at 1, +1 per committed change set
    text: str


class NotFound(LookupError):
    """No document has the requested id."""


class DocumentStore:
    """Documents in memory. Each one is an immutable snapshot; a write swaps in a new snapshot."""

    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}
        self._locks: dict[str, threading.Lock] = {}

    def add(self, title: str, text: str) -> Document:
        document = Document(str(uuid4()), title, 1, text)
        self._locks[document.id] = threading.Lock()
        self.put(document)
        return document

    def get(self, doc_id: str) -> Document:
        try:
            return self._documents[doc_id]
        except KeyError:
            raise NotFound(f"document {doc_id} not found") from None

    def lock(self, doc_id: str) -> threading.Lock:
        """The lock a writer holds to read, check and replace one document."""
        self.get(doc_id)
        return self._locks[doc_id]

    def all(self) -> list[Document]:
        """Every document, in the order it was added."""
        return list(self._documents.values())

    def put(self, document: Document) -> None:
        self._documents[document.id] = document
