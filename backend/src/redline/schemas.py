"""HTTP bodies and query parameters. Requests reject unknown fields, so a typo is an error."""

import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, BeforeValidator, Field, model_validator
from pydantic_core import PydanticCustomError

from redline.changes import Change, Edit, RangeChange

MAX_CHANGES = 1_000
SURROGATE = re.compile("[\ud800-\udfff]")


def unicode_text(value: object) -> object:
    # Before pydantic's own check, which gives a vaguer message and only on length-limited fields.
    if isinstance(value, str) and SURROGATE.search(value):
        message = "contains an unpaired surrogate, which is not valid Unicode text"
        raise PydanticCustomError("unicode", message)
    return value


def occurrence(value: object) -> object:
    if value is None or value == "all" or (type(value) is int and value >= 1):
        return value
    raise PydanticCustomError("occurrence", 'must be a whole number from 1 up, or "all"')


Text = Annotated[str, BeforeValidator(unicode_text)]
Occurrence = Annotated[int | Literal["all"] | None, BeforeValidator(occurrence)]


class Target(BaseModel, extra="forbid"):
    text: Text = Field(min_length=1)
    occurrence: Occurrence = None


class Range(BaseModel, extra="forbid"):
    start: int = Field(strict=True, ge=0)
    end: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def end_not_before_start(self) -> Self:
        if self.end < self.start:
            raise PydanticCustomError("range", "end must not be before start")
        return self


class ChangeSpec(BaseModel, extra="forbid"):
    """One change, in one of two forms: `target` and `replacement`, or `range` and `text`."""

    operation: Literal["replace"] = "replace"
    target: Target | None = None
    replacement: Text | None = None
    range: Range | None = None
    text: Text | None = None

    @model_validator(mode="after")
    def one_complete_form(self) -> Self:
        given = [
            value is not None for value in (self.target, self.replacement, self.range, self.text)
        ]
        if given not in ([True, True, False, False], [False, False, True, True]):
            raise PydanticCustomError(
                "change_form", 'give either "target" and "replacement", or "range" and "text"'
            )
        return self

    def change(self) -> Change | RangeChange:
        """The core change this describes."""
        if self.range is not None and self.text is not None:
            return RangeChange(self.range.start, self.range.end, self.text)
        assert self.target is not None and self.replacement is not None
        return Change(self.target.text, self.replacement, self.target.occurrence)


class ChangeSet(BaseModel, extra="forbid"):
    changes: list[ChangeSpec] = Field(min_length=1, max_length=MAX_CHANGES)


class EditOptions(BaseModel, extra="forbid"):
    dry_run: bool = False


class SearchQuery(BaseModel, extra="forbid"):
    q: str = Field(min_length=1)
    limit: int = Field(10, ge=1, le=100)
    offset: int = Field(0, ge=0)


class NewDocument(BaseModel, extra="forbid"):
    title: Text = Field("Untitled", max_length=200)
    text: Text


class Instruction(BaseModel, extra="forbid"):
    instruction: Text = Field(min_length=1, max_length=2_000)


class Error(BaseModel):
    error: str
    code: int


class Summary(BaseModel):
    id: str
    title: str
    version: int
    length: int


class DocumentList(BaseModel):
    documents: list[Summary]


class FullDocument(BaseModel):
    id: str
    title: str
    version: int
    text: str


class EditResponse(FullDocument):
    committed: bool
    edits: list[Edit]


class Span(BaseModel):
    start: int
    end: int


class SearchHit(BaseModel):
    document_id: str
    title: str
    version: int
    score: float
    start: int
    end: int
    snippet: str
    matches: list[Span]


class SearchResponse(BaseModel):
    query: str
    total: int
    limit: int
    offset: int
    hits: list[SearchHit]


class Suggestion(BaseModel):
    change: ChangeSpec
    rationale: str


class RejectedProposal(BaseModel):
    quote: str
    replacement: str
    rationale: str
    reason: str


class SuggestionsResponse(BaseModel):
    version: int
    suggestions: list[Suggestion]
    rejected: list[RejectedProposal]
