from typing import Any
from unittest.mock import ANY

import httpx2
import pytest
from fastapi.testclient import TestClient

from redline.api import create_app
from redline.service import MAX_SUGGEST_CHARS, Proposer, Service
from redline.suggest import ModelError, Proposal

TEXT = "1. Payment. Licensee pays Net 30.\n\n2. Late fees. Late invoices accrue Net 30.\n"
NET_45 = {"changes": [{"target": {"text": "Net 30", "occurrence": 1}, "replacement": "Net 45"}]}
PAYMENT = {"changes": [{"range": {"start": 3, "end": 10}, "text": "Price"}]}
JSON = {"Content-Type": "application/json"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Service()))


@pytest.fixture
def doc(client: TestClient) -> dict[str, Any]:
    response = client.post("/documents", json={"title": "Terms", "text": TEXT})
    body: dict[str, Any] = response.json()
    return body


def assert_error(response: httpx2.Response, status: int) -> None:
    assert response.status_code == status
    assert response.json() == {"error": ANY, "code": status}


def test_create_returns_a_summary_with_location_and_etag(client: TestClient) -> None:
    response = client.post("/documents", json={"title": "Terms", "text": TEXT})
    body = response.json()
    assert response.status_code == 201
    assert response.headers["Location"] == f"/documents/{body['id']}"
    assert response.headers["ETag"] == '"1"'
    assert body == {"id": ANY, "title": "Terms", "version": 1, "length": len(TEXT)}


def test_list_returns_summaries(client: TestClient, doc: dict[str, Any]) -> None:
    assert client.get("/documents").json() == {"documents": [doc]}


def test_get_returns_the_text_and_etag(client: TestClient, doc: dict[str, Any]) -> None:
    response = client.get(f"/documents/{doc['id']}")
    assert response.headers["ETag"] == '"1"'
    assert response.json() == {"id": doc["id"], "title": "Terms", "version": 1, "text": TEXT}


def test_patch_commits_and_returns_the_edits(client: TestClient, doc: dict[str, Any]) -> None:
    response = client.patch(f"/documents/{doc['id']}", json=NET_45, headers={"If-Match": '"1"'})
    body = response.json()
    assert response.status_code == 200
    assert response.headers["ETag"] == '"2"'
    assert (body["version"], body["committed"]) == (2, True)
    assert body["text"] == TEXT.replace("Net 30", "Net 45", 1)
    assert body["edits"] == [
        {"change": 0, "start": TEXT.index("Net 30"), "removed": "Net 30", "inserted": "Net 45"}
    ]


def test_a_dry_run_previews_without_committing_or_an_etag(
    client: TestClient, doc: dict[str, Any]
) -> None:
    response = client.patch(f"/documents/{doc['id']}?dry_run=true", json=NET_45)
    assert "ETag" not in response.headers
    assert (response.json()["version"], response.json()["committed"]) == (1, False)
    assert response.json()["text"] == TEXT.replace("Net 30", "Net 45", 1)
    assert client.get(f"/documents/{doc['id']}").json()["text"] == TEXT


def test_a_misspelt_query_parameter_is_422_and_commits_nothing(
    client: TestClient, doc: dict[str, Any]
) -> None:
    response = client.patch(f"/documents/{doc['id']}?dryrun=true", json=NET_45)
    assert_error(response, 422)
    assert response.json()["error"] == "query.dryrun: Extra inputs are not permitted"
    assert client.get(f"/documents/{doc['id']}").json()["version"] == 1


VERBATIM = [
    b'{"changes": [{"operation": "replace", "target": {"text": "old text", "occurrence": 1}, '
    b'"replacement": "new text"}]}',
    b'{"changes": [{"operation": "replace", "range": {"start": 100, "end": 108}, '
    b'"text": "new text"}]}',
]


@pytest.mark.parametrize("body", VERBATIM, ids=["target", "range"])
def test_both_forms_of_change_work_verbatim(client: TestClient, body: bytes) -> None:
    text = "Preamble. " * 10 + "old text, and more."
    doc = client.post("/documents", json={"text": text}).json()
    headers = {**JSON, "If-Match": '"1"'}
    response = client.patch(f"/documents/{doc['id']}", content=body, headers=headers)
    assert response.json()["text"] == text.replace("old text", "new text")


@pytest.mark.parametrize(
    "change",
    [
        {"target": {"text": "Net 30"}, "replacement": "Net 45", "range": {"start": 0, "end": 1}},
        {"target": {"text": "Net 30"}, "text": "Net 45"},
        {"target": {"text": "Net 30"}},
        {"range": {"start": 0, "end": 1}},
        {"operation": "replace"},
    ],
    ids=["both", "crossed", "no replacement", "no text", "neither"],
)
def test_a_change_must_be_exactly_one_of_the_two_forms(
    client: TestClient, doc: dict[str, Any], change: dict[str, Any]
) -> None:
    response = client.patch(f"/documents/{doc['id']}", json={"changes": [change]})
    assert_error(response, 422)
    assert response.json()["error"] == (
        'changes[0]: give either "target" and "replacement", or "range" and "text"'
    )


def test_a_range_change_without_if_match_is_428(client: TestClient, doc: dict[str, Any]) -> None:
    assert_error(client.patch(f"/documents/{doc['id']}", json=PAYMENT), 428)
    response = client.patch(f"/documents/{doc['id']}", json=PAYMENT, headers={"If-Match": "*"})
    assert_error(response, 428)


def test_a_range_change_with_if_match_applies(client: TestClient, doc: dict[str, Any]) -> None:
    response = client.patch(f"/documents/{doc['id']}", json=PAYMENT, headers={"If-Match": '"1"'})
    assert response.json()["text"] == TEXT.replace("Payment", "Price")


def test_an_empty_range_inserts(client: TestClient, doc: dict[str, Any]) -> None:
    insertion = {"changes": [{"range": {"start": 10, "end": 10}, "text": " terms"}]}
    response = client.patch(f"/documents/{doc['id']}", json=insertion, headers={"If-Match": '"1"'})
    assert response.json()["text"] == TEXT.replace("Payment", "Payment terms", 1)
    assert response.json()["edits"] == [
        {"change": 0, "start": 10, "removed": "", "inserted": " terms"}
    ]


@pytest.mark.parametrize("if_match", ["1", "*"])
def test_a_bare_or_star_if_match_is_accepted(
    client: TestClient, doc: dict[str, Any], if_match: str
) -> None:
    response = client.patch(f"/documents/{doc['id']}", json=NET_45, headers={"If-Match": if_match})
    assert response.status_code == 200


@pytest.mark.parametrize("if_match", ['W/"1"', "one", "1, 2"])
def test_a_malformed_if_match_is_422_with_a_plain_message(
    client: TestClient, doc: dict[str, Any], if_match: str
) -> None:
    response = client.patch(f"/documents/{doc['id']}", json=NET_45, headers={"If-Match": if_match})
    assert_error(response, 422)
    assert response.json()["error"] == 'If-Match must be one version in quotes, like "3", or *'


def test_an_unknown_document_is_404(client: TestClient) -> None:
    assert_error(client.get("/documents/nope"), 404)


def test_suggestions_for_an_unknown_document_are_404_even_without_a_model(
    client: TestClient,
) -> None:
    assert_error(client.post("/documents/nope/suggestions", json={"instruction": "Net 45"}), 404)


def test_an_unknown_route_is_404(client: TestClient) -> None:
    assert_error(client.get("/contracts"), 404)


def test_an_unsupported_method_is_405(client: TestClient) -> None:
    assert_error(client.delete("/documents"), 405)


def test_a_change_that_does_not_fit_is_409(client: TestClient, doc: dict[str, Any]) -> None:
    ambiguous = {"changes": [{"target": {"text": "Net 30"}, "replacement": "Net 45"}]}
    response = client.patch(f"/documents/{doc['id']}", json=ambiguous)
    assert_error(response, 409)
    assert response.json()["error"] == (
        'changes[0]: "Net 30" appears 2 times and no occurrence was given'
    )


def test_a_stale_if_match_is_412(client: TestClient, doc: dict[str, Any]) -> None:
    path = f"/documents/{doc['id']}"
    client.patch(path, json=NET_45)
    assert_error(client.patch(path, json=NET_45, headers={"If-Match": "1"}), 412)


@pytest.mark.parametrize(
    "change",
    [
        {"target": {"text": "Net 30", "occurence": 1}, "replacement": "Net 45"},
        {"target": {"text": ""}, "replacement": "Net 45"},
        {"operation": "delete", "target": {"text": "Net 30"}, "replacement": ""},
        {"range": {"start": -1, "end": 3}, "text": "x"},
        {"range": {"start": 1.0, "end": 3}, "text": "x"},
    ],
)
def test_an_invalid_change_is_422(
    client: TestClient, doc: dict[str, Any], change: dict[str, Any]
) -> None:
    assert_error(client.patch(f"/documents/{doc['id']}", json={"changes": [change]}), 422)


@pytest.mark.parametrize("occurrence", [0, "2", True, 2.5])
def test_an_invalid_occurrence_has_one_clear_message(
    client: TestClient, doc: dict[str, Any], occurrence: object
) -> None:
    change = {"target": {"text": "Net 30", "occurrence": occurrence}, "replacement": "Net 45"}
    response = client.patch(f"/documents/{doc['id']}", json={"changes": [change]})
    assert_error(response, 422)
    assert response.json()["error"] == (
        'changes[0].target.occurrence: must be a whole number from 1 up, or "all"'
    )


def test_more_than_a_thousand_changes_is_422(client: TestClient, doc: dict[str, Any]) -> None:
    response = client.patch(f"/documents/{doc['id']}", json={"changes": NET_45["changes"] * 1_001})
    assert_error(response, 422)


def test_a_misspelt_field_is_named_in_the_error(client: TestClient, doc: dict[str, Any]) -> None:
    change = {"target": {"text": "Net 30", "occurence": 1}, "replacement": "Net 45"}
    response = client.patch(f"/documents/{doc['id']}", json={"changes": [change]})
    assert response.json()["error"] == "changes[0].target.occurence: Extra inputs are not permitted"


def test_malformed_json_is_400(client: TestClient, doc: dict[str, Any]) -> None:
    response = client.patch(f"/documents/{doc['id']}", content=b'{"changes": [', headers=JSON)
    assert_error(response, 400)
    assert response.json()["error"] == "malformed JSON at character 13: Expecting value"


@pytest.mark.parametrize("content_type", [None, "application/x-www-form-urlencoded"])
def test_a_body_not_sent_as_json_is_415(client: TestClient, content_type: str | None) -> None:
    headers = {} if content_type is None else {"Content-Type": content_type}
    response = client.post("/documents", content=b'{"text": "Net 30"}', headers=headers)
    assert response.status_code == 415
    assert response.json() == {
        "error": "the body must be JSON: send Content-Type: application/json",
        "code": 415,
    }


def test_an_unpaired_surrogate_is_422(client: TestClient, doc: dict[str, Any]) -> None:
    change = b'{"changes": [{"target": {"text": "Net 30"}, "replacement": "\\udc00"}]}'
    responses = [
        client.post("/documents", content=b'{"text": "Net \\ud800 30"}', headers=JSON),
        client.patch(f"/documents/{doc['id']}", content=change, headers=JSON),
    ]
    for response in responses:
        assert_error(response, 422)
        assert response.json()["error"].endswith(
            ": contains an unpaired surrogate, which is not valid Unicode text"
        )


def failing_proposer(error: Exception) -> Proposer:
    def propose(instruction: str, text: str) -> list[Proposal]:
        raise error

    return propose


def test_an_oversized_document_is_413() -> None:
    client = TestClient(create_app(Service(failing_proposer(AssertionError()))))
    doc = client.post("/documents", json={"text": "x" * (MAX_SUGGEST_CHARS + 1)}).json()
    response = client.post(f"/documents/{doc['id']}/suggestions", json={"instruction": "Net 45"})
    assert_error(response, 413)


def test_a_model_failure_is_502() -> None:
    client = TestClient(create_app(Service(failing_proposer(ModelError("the model is down")))))
    doc = client.post("/documents", json={"text": TEXT}).json()
    response = client.post(f"/documents/{doc['id']}/suggestions", json={"instruction": "Net 45"})
    assert_error(response, 502)


def test_an_unhandled_exception_is_500_without_internals() -> None:
    service = Service(failing_proposer(RuntimeError("secret detail")))
    client = TestClient(create_app(service), raise_server_exceptions=False)
    doc = client.post("/documents", json={"text": TEXT}).json()
    response = client.post(f"/documents/{doc['id']}/suggestions", json={"instruction": "Net 45"})
    assert response.status_code == 500
    assert response.json() == {"error": "internal error", "code": 500}


def test_accepted_suggestions_can_be_sent_back_as_the_patch_body() -> None:
    proposals = [
        Proposal(quote="pays Net 30", replacement="pays Net 45", rationale="Net 45."),
        Proposal(quote="Net 30", replacement="Net 45", rationale="Everywhere."),
    ]
    client = TestClient(create_app(Service(lambda instruction, text: proposals)))
    doc = client.post("/documents", json={"text": TEXT}).json()
    path = f"/documents/{doc['id']}"
    body = client.post(f"{path}/suggestions", json={"instruction": "Net 45"}).json()
    target = {"text": "pays Net 30"}
    change = {"operation": "replace", "target": target, "replacement": "pays Net 45"}
    assert body == {
        "version": 1,
        "suggestions": [{"change": change, "rationale": "Net 45."}],
        "rejected": [
            {
                "quote": "Net 30",
                "replacement": "Net 45",
                "rationale": "Everywhere.",
                "reason": '"Net 30" appears 2 times and no occurrence was given',
            }
        ],
    }
    response = client.patch(path, json={"changes": [change]}, headers={"If-Match": '"1"'})
    assert response.json()["text"] == TEXT.replace("pays Net 30", "pays Net 45")


def test_search_returns_hits_with_snippets_and_matches(
    client: TestClient, doc: dict[str, Any]
) -> None:
    body = client.get("/documents/search", params={"q": "late fees"}).json()
    assert (body["query"], body["total"], body["limit"], body["offset"]) == ("late fees", 1, 10, 0)
    hit = body["hits"][0]
    assert hit["snippet"] == TEXT[hit["start"] : hit["end"]] == TEXT.splitlines()[2]
    assert [TEXT[m["start"] : m["end"]] for m in hit["matches"]] == ["Late", "fees", "Late"]


def test_search_can_be_scoped_to_one_document(client: TestClient, doc: dict[str, Any]) -> None:
    other = client.post("/documents", json={"title": "Copy", "text": TEXT}).json()
    everywhere = client.get("/documents/search", params={"q": "payment"}).json()
    scoped = client.get(f"/documents/{other['id']}/search", params={"q": "payment"}).json()
    assert everywhere["total"] == 2
    assert [hit["document_id"] for hit in scoped["hits"]] == [other["id"]]


def test_search_pages_with_limit_and_offset(client: TestClient, doc: dict[str, Any]) -> None:
    client.post("/documents", json={"title": "Copy", "text": TEXT})
    first = client.get("/documents/search", params={"q": "net", "limit": 1}).json()
    second = client.get("/documents/search", params={"q": "net", "limit": 1, "offset": 1}).json()
    everything = client.get("/documents/search", params={"q": "net"}).json()
    assert first["total"] == second["total"] == 4
    assert first["hits"] + second["hits"] == everything["hits"][:2]


def test_search_in_an_unknown_document_is_404(client: TestClient) -> None:
    assert_error(client.get("/documents/nope/search", params={"q": "net"}), 404)


@pytest.mark.parametrize(
    "params", [{}, {"q": ""}, {"q": "net", "limit": 101}, {"q": "net", "document_id": "x"}]
)
def test_a_bad_search_query_is_422(
    client: TestClient, doc: dict[str, Any], params: dict[str, Any]
) -> None:
    assert_error(client.get("/documents/search", params=params), 422)
    assert_error(client.get(f"/documents/{doc['id']}/search", params=params), 422)
