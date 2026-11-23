from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import ANY

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from redline.api import create_app, default_service
from redline.changes import Change
from redline.service import (
    MAX_SUGGEST_CHARS,
    Proposer,
    Rejected,
    Service,
    Suggestion,
    Suggestions,
    TooLarge,
    Unavailable,
)
from redline.suggest import (
    MODEL,
    SYSTEM,
    TIMEOUT,
    ModelError,
    Proposal,
    Proposals,
    new_client,
    propose,
)

TEXT = "4. Payment. Licensee pays within Net 30 days. Late fees accrue after Net 30."
REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def proposer(*proposals: Proposal) -> Proposer:
    return lambda instruction, text: list(proposals)


def proposal(quote: str, replacement: str) -> Proposal:
    return Proposal(quote=quote, replacement=replacement, rationale="Per the instruction.")


def suggest(*proposals: Proposal) -> tuple[Service, Suggestions]:
    service = Service(proposer(*proposals))
    document = service.create("Terms", TEXT)
    return service, service.suggest(document.id, "Make payment terms Net 45")


def test_accepted_suggestions_apply_cleanly_with_if_match() -> None:
    service, result = suggest(
        proposal("within Net 30 days", "within Net 45 days"),
        proposal("after Net 30", "after Net 45"),
    )
    changes = [suggestion.change for suggestion in result.accepted]
    edited = service.edit(service.documents()[0].id, changes, if_match=result.version)
    assert edited.document.text == TEXT.replace("Net 30", "Net 45")
    assert result.rejected == []


@pytest.mark.parametrize(
    ("quote", "reason"),
    [
        ("Net 60", '"Net 60" does not appear in the document'),
        ("Net 30", '"Net 30" appears 2 times and no occurrence was given'),
        ("", "the text to replace is empty"),
    ],
)
def test_a_quote_that_does_not_identify_one_place_is_rejected(quote: str, reason: str) -> None:
    _, result = suggest(proposal(quote, "Net 45"), proposal("after Net 30", "after Net 45"))
    assert [rejected.reason for rejected in result.rejected] == [reason]
    assert [suggestion.change.text for suggestion in result.accepted] == ["after Net 30"]


def test_an_overlap_names_the_suggestion_it_overlaps_by_its_place_in_the_suggestions() -> None:
    _, result = suggest(
        proposal("Net 60", "Net 45"),
        proposal("within Net 30 days", "within 45 days"),
        proposal("Net 30 days", "x"),
    )
    assert [suggestion.change.text for suggestion in result.accepted] == ["within Net 30 days"]
    assert [rejected.reason for rejected in result.rejected][1] == (
        f"overlaps changes[0] at offset {TEXT.index('Net 30 days')}"
    )


def test_suggestions_keep_each_rationale_and_each_rejected_proposal() -> None:
    late = Proposal(quote="after Net 30", replacement="after Net 45", rationale="Match it.")
    missing = Proposal(quote="Net 60", replacement="Net 45", rationale="Shorten it.")
    _, result = suggest(late, missing)
    assert result.accepted == [Suggestion(Change("after Net 30", "after Net 45"), "Match it.")]
    assert result.rejected == [Rejected(missing, '"Net 60" does not appear in the document')]


def test_suggestions_never_write() -> None:
    service, _ = suggest(proposal("within Net 30 days", "within Net 45 days"))
    assert (service.documents()[0].version, service.documents()[0].text) == (1, TEXT)


def test_the_model_gets_the_instruction_and_the_current_text() -> None:
    seen: list[tuple[str, str]] = []

    def record(instruction: str, text: str) -> list[Proposal]:
        seen.append((instruction, text))
        return []

    service = Service(record)
    document = service.create("Terms", TEXT)
    service.edit(document.id, [Change("within Net 30 days", "within Net 45 days")])
    assert service.suggest(document.id, "Make late fees Net 45").version == 2
    assert seen == [("Make late fees Net 45", TEXT.replace("within Net 30", "within Net 45"))]


def test_without_a_proposer_suggestions_are_unavailable() -> None:
    service = Service()
    document = service.create("Terms", TEXT)
    with pytest.raises(Unavailable, match="set ANTHROPIC_API_KEY and restart$"):
        service.suggest(document.id, "Make payment terms Net 45")
    client = TestClient(create_app(service))
    response = client.post(f"/documents/{document.id}/suggestions", json={"instruction": "Net 45"})
    assert response.status_code == 503


def test_an_oversized_document_is_too_large() -> None:
    service = Service(proposer())
    document = service.create("Huge", "x" * (MAX_SUGGEST_CHARS + 1))
    with pytest.raises(TooLarge, match="^document has 200,001 characters; .* up to 200,000$"):
        service.suggest(document.id, "Make payment terms Net 45")


def test_a_document_at_the_size_limit_gets_suggestions() -> None:
    service = Service(proposer())
    document = service.create("Limit", "x" * MAX_SUGGEST_CHARS)
    assert service.suggest(document.id, "Make payment terms Net 45") == Suggestions(1, [], [])


def stub(result: object) -> tuple[anthropic.Anthropic, list[dict[str, Any]]]:
    """A client whose messages.parse records its arguments, then returns or raises `result`."""
    calls: list[dict[str, Any]] = []

    def parse(**kwargs: Any) -> object:
        calls.append(kwargs)
        if isinstance(result, Exception):
            raise result
        return result

    return cast(anthropic.Anthropic, SimpleNamespace(messages=SimpleNamespace(parse=parse))), calls


def cut_off_answer() -> ValidationError:
    """What parse() raises when the answer stops at max_tokens: its JSON is incomplete."""
    try:
        Proposals.model_validate_json('{"proposals": [{"quote": "Net')
    except ValidationError as error:
        return error
    raise AssertionError("incomplete JSON was accepted")


def test_propose_sends_the_contract_and_instruction_and_returns_the_proposals() -> None:
    proposals = [proposal("within Net 30 days", "within Net 45 days")]
    client, calls = stub(SimpleNamespace(parsed_output=Proposals(proposals=proposals)))
    assert propose(client, "Make payment terms Net 45", TEXT) == proposals
    [call] = calls
    assert (call["model"], call["system"], call["output_format"]) == (MODEL, SYSTEM, Proposals)
    content = call["messages"][0]["content"]
    assert content.index(f"<contract>\n{TEXT}\n</contract>") < content.index(
        "<instruction>\nMake payment terms Net 45\n</instruction>"
    )


UNUSABLE = [
    SimpleNamespace(parsed_output=None),
    SimpleNamespace(),
    cut_off_answer(),
    ValueError("an SDK guard"),
    AttributeError("'NoneType' object has no attribute 'content'"),
    TypeError("'NoneType' object is not iterable"),
    RecursionError("maximum recursion depth exceeded"),
    anthropic.APIConnectionError(request=REQUEST),
    anthropic.APITimeoutError(request=REQUEST),
]
UNUSABLE_IDS = [
    "no answer",
    "not a message",
    "cut off",
    "value error",
    "attribute error",
    "type error",
    "recursion error",
    "connection",
    "timeout",
]


@pytest.mark.parametrize("result", UNUSABLE, ids=UNUSABLE_IDS)
def test_propose_turns_an_unusable_answer_into_a_model_error(result: object) -> None:
    client, _ = stub(result)
    with pytest.raises(ModelError):
        propose(client, "Make payment terms Net 45", TEXT)


@pytest.mark.parametrize("result", UNUSABLE, ids=UNUSABLE_IDS)
def test_an_unusable_answer_is_a_502(result: object) -> None:
    client, _ = stub(result)
    api = TestClient(create_app(Service(partial(propose, client))))
    doc = api.post("/documents", json={"text": TEXT}).json()
    response = api.post(f"/documents/{doc['id']}/suggestions", json={"instruction": "Net 45"})
    assert response.status_code == 502
    assert response.json() == {"error": ANY, "code": 502}
    assert response.json()["error"].startswith("the model")


def test_a_new_client_makes_one_attempt_with_a_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "never-used")
    client = new_client()
    assert isinstance(client.timeout, anthropic.Timeout)
    assert (client.timeout.connect, client.timeout.read, client.max_retries) == (5.0, TIMEOUT, 0)
    assert TIMEOUT == 120.0


def seed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "terms.txt").write_text(f"\n  Payment Terms  \n\n{TEXT}\n", encoding="utf-8")
    monkeypatch.setenv("REDLINE_SEED", str(tmp_path))


def test_the_default_service_loads_the_seed_files_without_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(tmp_path, monkeypatch)
    (tmp_path / "with-bom.txt").write_text("Order Form\nNet 30.\n", encoding="utf-8-sig")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    service = default_service()
    document, with_bom = service.documents()
    assert (document.title, document.text) == ("Payment Terms", f"\n  Payment Terms  \n\n{TEXT}\n")
    assert (with_bom.title, with_bom.text) == ("Order Form", "Order Form\nNet 30.\n")
    with pytest.raises(Unavailable):
        service.suggest(document.id, "Make payment terms Net 45")


def test_the_default_service_has_a_model_when_a_key_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "never-used")
    service = default_service()
    huge = service.create("Huge", "x" * (MAX_SUGGEST_CHARS + 1))
    # Only a configured model gets as far as the size check, and that check makes no call.
    with pytest.raises(TooLarge):
        service.suggest(huge.id, "Make payment terms Net 45")


def test_the_default_app_starts_empty_without_a_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDLINE_SEED", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert TestClient(create_app()).get("/documents").json() == {"documents": []}
