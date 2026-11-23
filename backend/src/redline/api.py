"""The HTTP API. Handlers translate between HTTP and the service and do nothing else."""

import os
from collections.abc import Mapping
from dataclasses import asdict
from functools import partial
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
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

router = APIRouter()


def create_app(service: Service | None = None) -> FastAPI:
    """The app around `service`, or around the default service when none is given."""
    # FastAPI documents 422 with its own error body; every error here has the Error body.
    app = FastAPI(title="Redline", responses={422: {"model": schemas.Error}})
    app.state.service = default_service() if service is None else service
    app.include_router(router)
    for handled in (*STATUS, RequestValidationError, HTTPException, Exception):
        app.add_exception_handler(handled, error_response)
    return app


def get_service(request: Request) -> Service:
    service: Service = request.app.state.service
    return service


ServiceDep = Annotated[Service, Depends(get_service)]


@router.post("/documents", status_code=201)
def create_document(
    body: schemas.NewDocument, response: Response, service: ServiceDep
) -> schemas.Summary:
    """Create a document from a title and a text."""
    document = service.create(body.title, body.text)
    response.headers["Location"] = f"/documents/{document.id}"
    response.headers["ETag"] = etag(document)
    return summary(document)


@router.get("/documents")
def list_documents(service: ServiceDep) -> schemas.DocumentList:
    """List every document, without its text."""
    return schemas.DocumentList(documents=[summary(d) for d in service.documents()])


# Registered before /documents/{doc_id}, which would otherwise take "search" as an id.
@router.get("/documents/search")
def search_documents(
    query: Annotated[schemas.SearchQuery, Query()], service: ServiceDep
) -> schemas.SearchResponse:
    """Find the blocks of every document that contain all the words of `q`, best first."""
    return search_response(service, query)


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, response: Response, service: ServiceDep) -> schemas.FullDocument:
    """Fetch a document's text, with its version as the ETag."""
    document = service.get(doc_id)
    response.headers["ETag"] = etag(document)
    return schemas.FullDocument(**asdict(document))


@router.patch("/documents/{doc_id}")
def edit_document(
    doc_id: str,
    body: schemas.ChangeSet,
    options: Annotated[schemas.EditOptions, Query()],
    response: Response,
    service: ServiceDep,
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


@router.get("/documents/{doc_id}/search")
def search_document(
    doc_id: str, query: Annotated[schemas.SearchQuery, Query()], service: ServiceDep
) -> schemas.SearchResponse:
    """Find the blocks of one document that contain all the words of `q`, best first."""
    return search_response(service, query, doc_id)


@router.post("/documents/{doc_id}/suggestions", response_model_exclude_none=True)
def suggest_changes(
    doc_id: str, body: schemas.Instruction, service: ServiceDep
) -> schemas.SuggestionsResponse:
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


def error_response(request: Request, exc: Exception) -> JSONResponse:
    """Every failure leaves as {"error": ..., "code": ...}."""
    if isinstance(exc, RequestValidationError):
        detail = exc.errors()[0]
        if detail["type"] == "json_invalid":
            return error(400, describe(detail))
        if detail["loc"] == ("body",) and not is_json(request.headers.get("content-type", "")):
            return error(415, "the body must be JSON: send Content-Type: application/json")
        return error(422, describe(detail))
    if isinstance(exc, HTTPException):  # an unknown route, a wrong method, a bad If-Match
        return error(exc.status_code, exc.detail, exc.headers)
    if type(exc) in STATUS:
        return error(STATUS[type(exc)], str(exc))
    return error(500, "internal error")


def parse_if_match(header: str | None) -> int | None:
    """The version If-Match names, or None for no check (no header, or *)."""
    if header is None or header == "*":
        return None
    try:
        return int(header.strip('"'))
    except ValueError:
        raise HTTPException(422, 'If-Match must be one version in quotes, like "3", or *') from None


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


def search_response(
    service: Service, query: schemas.SearchQuery, doc_id: str | None = None
) -> schemas.SearchResponse:
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
