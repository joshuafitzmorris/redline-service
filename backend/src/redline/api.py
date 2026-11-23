"""The HTTP API. Handlers translate between HTTP and the service and do nothing else."""

import os
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from redline import schemas
from redline.changes import ChangeError
from redline.service import Service, TooLarge, Unavailable, VersionConflict, VersionRequired
from redline.store import Document, NotFound
from redline.suggest import ModelError, new_client, propose

STATUS: dict[type[Exception], int] = {
    NotFound: 404,
    ChangeError: 409,
    VersionConflict: 412,
    TooLarge: 413,
    VersionRequired: 428,
    ModelError: 502,
    Unavailable: 503,
}

ERRORS = {
    400: "The body is not valid JSON",
    404: "No such document",
    409: "A change does not fit the document",
    412: "If-Match is not the current version",
    413: "The document is too large for suggestions",
    415: "The body was not sent as JSON",
    422: "The request is invalid",
    428: "A range change needs If-Match",
    502: "The model call failed",
    503: "Suggestions are not configured",
}

# int() refuses more than 4,300 digits, and no version has more than 18.
VERSION = re.compile(r'"([0-9]{1,18})"|([0-9]{1,18})')


def create_app(service: Service | None = None) -> FastAPI:
    """The app around `service`, or around the default service when none is given."""
    if service is None:
        service = default_service()
    app = FastAPI(title="Redline", responses=responses(422), dependencies=[Depends(single_valued)])

    def domain_error(request: Request, exc: Exception) -> JSONResponse:
        return error(STATUS[type(exc)], str(exc))

    for exception_type in STATUS:
        app.add_exception_handler(exception_type, domain_error)

    @app.exception_handler(Exception)
    def internal_error(request: Request, exc: Exception) -> JSONResponse:
        return error(500, "internal error")

    @app.exception_handler(RequestValidationError)
    def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = exc.errors()[0]
        if detail["type"] == "json_invalid":
            return error(400, describe(detail))
        if detail["loc"] == ("body",) and not is_json(request.headers.get("content-type", "")):
            return error(415, "the body must be JSON: send Content-Type: application/json")
        return error(422, describe(detail))

    @app.exception_handler(HTTPException)
    def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error(exc.status_code, exc.detail, exc.headers)

    @app.post("/documents", status_code=201, responses=responses(400, 415))
    def create_document(body: schemas.NewDocument, response: Response) -> schemas.Summary:
        """Create a document from a title and a text."""
        document = service.create(body.title, body.text)
        response.headers["Location"] = f"/documents/{document.id}"
        response.headers["ETag"] = etag(document)
        return summary(document)

    @app.get("/documents")
    def list_documents() -> schemas.DocumentList:
        """List every document, without its text."""
        return schemas.DocumentList(documents=[summary(d) for d in service.documents()])

    def search_response(query: schemas.SearchQuery, doc_id: str | None) -> schemas.SearchResponse:
        total, hits = service.search(query.q, doc_id, limit=query.limit, offset=query.offset)
        return schemas.SearchResponse(
            query=query.q,
            total=total,
            limit=query.limit,
            offset=query.offset,
            hits=[
                schemas.SearchHit(
                    document_id=document.id,
                    title=document.title,
                    version=document.version,
                    score=hit.score,
                    start=hit.block.start,
                    end=hit.block.end,
                    snippet=document.text[hit.block.start : hit.block.end],
                    matches=[schemas.Span(start=m.start, end=m.end) for m in hit.matches],
                )
                for document, hit in hits
            ],
        )

    # Registered before /documents/{doc_id}, which would otherwise take "search" as an id.
    @app.get("/documents/search")
    def search_documents(query: Annotated[schemas.SearchQuery, Query()]) -> schemas.SearchResponse:
        """Find the blocks of every document that contain all the words of `q`, best first."""
        return search_response(query, None)

    @app.get("/documents/{doc_id}", responses=responses(404))
    def get_document(doc_id: str, response: Response) -> schemas.FullDocument:
        """Fetch a document's text, with its version as the ETag."""
        document = service.get(doc_id)
        response.headers["ETag"] = etag(document)
        return schemas.FullDocument(**asdict(document))

    @app.patch("/documents/{doc_id}", responses=responses(400, 404, 409, 412, 415, 428))
    def edit_document(
        doc_id: str,
        body: schemas.ChangeSet,
        options: Annotated[schemas.EditOptions, Query()],
        response: Response,
        if_match: Annotated[str | None, Header()] = None,
    ) -> schemas.EditResponse:
        """Apply a change set all or nothing, or preview it with dry_run=true."""
        changes = [spec.change() for spec in body.changes]
        version = parse_if_match(if_match)
        result = service.edit(doc_id, changes, if_match=version, dry_run=options.dry_run)
        document = result.document
        if result.committed:
            response.headers["ETag"] = etag(document)
        return schemas.EditResponse(
            id=document.id,
            title=document.title,
            version=document.version,
            text=result.text,
            committed=result.committed,
            edits=result.edits,
        )

    @app.get("/documents/{doc_id}/search", responses=responses(404))
    def search_document(
        doc_id: str, query: Annotated[schemas.SearchQuery, Query()]
    ) -> schemas.SearchResponse:
        """Find the blocks of one document that contain all the words of `q`, best first."""
        return search_response(query, doc_id)

    @app.post(
        "/documents/{doc_id}/suggestions",
        response_model_exclude_none=True,
        responses=responses(400, 404, 413, 415, 502, 503),
    )
    def suggest_changes(doc_id: str, body: schemas.Instruction) -> schemas.SuggestionsResponse:
        """Ask the model for changes that carry out an instruction; nothing is written."""
        result = service.suggest(doc_id, body.instruction)
        return schemas.SuggestionsResponse(
            version=result.version,
            suggestions=[
                schemas.Suggestion(
                    change=schemas.ChangeSpec(
                        target=schemas.Target(text=s.change.text, occurrence=s.change.occurrence),
                        replacement=s.change.replacement,
                    ),
                    rationale=s.rationale,
                )
                for s in result.accepted
            ],
            rejected=[
                schemas.RejectedProposal(**r.proposal.model_dump(), reason=r.reason)
                for r in result.rejected
            ],
        )

    return app


def default_service() -> Service:
    """A model when ANTHROPIC_API_KEY is set, and the *.txt files in REDLINE_SEED as documents."""
    proposer = partial(propose, new_client()) if os.environ.get("ANTHROPIC_API_KEY") else None
    service = Service(proposer)
    if seed := os.environ.get("REDLINE_SEED"):
        for path in sorted(Path(seed).glob("*.txt")):
            text = path.read_text(encoding="utf-8-sig")
            title = next((line.strip() for line in text.splitlines() if line.strip()), path.stem)
            service.create(title, text)
    return service


def responses(*codes: int) -> dict[int | str, dict[str, Any]]:
    """The OpenAPI description of these error responses, all with the `Error` body."""
    return {code: {"model": schemas.Error, "description": ERRORS[code]} for code in codes}


def single_valued(request: Request) -> None:
    """Refuse a query string that gives one parameter twice, so that no value silently wins."""
    counts = Counter(name for name, _ in request.query_params.multi_items())
    for name, count in counts.items():
        if count > 1:
            raise HTTPException(422, f"query.{name}: given more than once")


def parse_if_match(header: str | None) -> int | None:
    """The version If-Match names, or None for no check (no header, or *)."""
    if header is None or header == "*":
        return None
    match = VERSION.fullmatch(header)
    if match is None:
        raise HTTPException(422, 'If-Match must be one version in quotes, like "3", or *')
    return int(match[1] or match[2])


def is_json(content_type: str) -> bool:
    media_type = content_type.partition(";")[0].strip().lower()
    return media_type.startswith("application/") and media_type.endswith(("/json", "+json"))


def error(status: int, message: str, headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": message, "code": status}, status_code=status, headers=headers)


def describe(detail: dict[str, Any]) -> str:
    """A validation error as "changes[0].target.text: <message>", without the leading "body"."""
    if detail["type"] == "json_invalid":
        return f"malformed JSON at character {detail['loc'][1]}: {detail['ctx']['error']}"
    location = detail["loc"][1:] if detail["loc"][0] == "body" else detail["loc"]
    path = "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in location)
    return f"{path.lstrip('.') or 'body'}: {detail['msg']}"


def etag(document: Document) -> str:
    return f'"{document.version}"'


def summary(document: Document) -> schemas.Summary:
    return schemas.Summary(
        id=document.id, title=document.title, version=document.version, length=len(document.text)
    )
