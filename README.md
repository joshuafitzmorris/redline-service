# Redline

A small service for redlining contracts. You send change requests as JSON and get back the updated text and the list of edits. You can search one document or all of them and get the matching clauses, ranked. A model can propose changes, and the service checks each one before a person sees it.

The API is Python and FastAPI, the UI is React and TypeScript, and documents are plain text held in memory. [INFRA.md](INFRA.md) covers what production would need.

![A change set previewed as a redline](docs/screenshot.png)

## Run it

Needs [uv](https://docs.astral.sh/uv/) and Node 22.12+ or 24+.

```sh
make install
make dev      # API on :8000, UI on http://localhost:5173, two sample contracts loaded
```

`make test`, `make lint` and `make bench` do what they say. Interactive API docs are at http://localhost:8000/docs, and `examples/requests.sh` walks the API with curl (it needs `jq`).

Suggestions need a key: `export ANTHROPIC_API_KEY=...` before `make dev`. Everything else works without one. The model defaults to `claude-opus-5-5` and can be changed with `REDLINE_MODEL`.

## The design in five lines

- There is one way to change a document. A change typed by a person, a bulk replace and a suggestion from the model are the same objects on the same path: matched against one base text, checked the same way, and applied in a single pass or not at all.
- An ambiguous target is an error. Text that appears three times has to say which occurrence it means.
- Every document has a version. A change can say which version it was made against, and a stale one is refused.
- The model can only propose. Its quotes go through the same check as any other change, and nothing is written until a person applies it.
- Documents are plain text in memory. Word files, storage and auth are out of scope and listed at the end.

```
UI --> api.py --> service.py --+--> changes.py   resolve and apply changes   (pure, standard library only)
       HTTP only  use cases    +--> search.py    blocks, index, ranking      (pure, standard library only)
                               +--> store.py     documents in memory
                               +--> suggest.py   the model call
```

## API

| | |
|---|---|
| `POST /documents` | create |
| `GET /documents` | list |
| `GET /documents/{id}` | text and version (`ETag`) |
| `PATCH /documents/{id}` | apply a change set; `?dry_run=true` previews it |
| `GET /documents/search?q=` | search every document |
| `GET /documents/{id}/search?q=` | search one document |
| `POST /documents/{id}/suggestions` | ask the model for changes; never writes |

Why it has this shape:

- A document changes only through `PATCH` with a change set, so the version check, the validation and the list of edits live in one place. An action endpoint per operation (`/replace`, `/replace-all`, `/preview`) would need a copy of each.
- Preview is a flag on the same request, so what you preview is what you commit.
- Suggestions are the one action-shaped call, a POST because it costs money and is not repeatable. It returns changes and leaves the write to `PATCH`.

### Changes

```sh
ID=$(curl -s localhost:8000/documents | jq -r '.documents[0].id')

curl -X PATCH localhost:8000/documents/$ID \
  -H 'Content-Type: application/json' -H 'If-Match: "1"' \
  -d '{"changes": [
        {"target": {"text": "Net 30", "occurrence": 2}, "replacement": "Net 45"},
        {"target": {"text": "Licensee", "occurrence": "all"}, "replacement": "Customer"}
      ]}'
```

```json
{
  "id": "...", "title": "Software License Agreement", "version": 2, "committed": true,
  "text": "Software License Agreement\n\n1. Parties. This ...",
  "edits": [
    {"change": 1, "start": 176, "removed": "Licensee", "inserted": "Customer"},
    {"change": 1, "start": 275, "removed": "Licensee", "inserted": "Customer"},
    {"change": 0, "start": 924, "removed": "Net 30", "inserted": "Net 45"}
  ]
}
```

(Sixteen edits in all; three are shown.)

A change names its target in one of two ways:

- By quoted text: `{"target": {"text": ..., "occurrence": ...}, "replacement": ...}`. `occurrence` is a number, `"all"`, or left out. Left out, the text has to match exactly once. I would sooner reject an edit to a contract than guess which clause it meant.
- By position: `{"range": {"start": ..., "end": ...}, "text": ...}`, where an empty range inserts. Offsets count Unicode code points and only mean something in one version of the document, so this form has to send `If-Match` and is a 428 without it.

The rules for both:

- Every change is matched against the document as it was before the request, so one change can never match text another inserted.
- All or nothing. If one change fails, none is applied, and the error names it (`changes[1]: ...`). Changes that touch the same text are rejected.
- Matching is exact, so a straight apostrophe does not match a curly one.
- `edits` is the redline: what was removed, what replaced it and where, in document order.
- Unknown fields and query parameters are a 422, so a misspelt `dry_run` cannot turn a preview into a commit.

### Versions

Each document has a version number, returned as an `ETag`. Send it back in `If-Match` and the change applies only if nobody else got there first; otherwise it is a 412. A dry run checks it the same way. `If-Match` is optional for a change by quoted text, which then applies to the current version.

Here a lock per document makes the check and the write one step. In Postgres the same check is `UPDATE ... WHERE id = $1 AND version = $2`.

### Search

```sh
curl -G localhost:8000/documents/$ID/search --data-urlencode 'q=limitation liability' -d limit=5
```

```json
{
  "query": "limitation liability", "total": 1, "limit": 5, "offset": 0,
  "hits": [
    {
      "document_id": "...", "title": "Software License Agreement", "version": 2, "score": 4.99,
      "start": 1835, "end": 2174,
      "snippet": "9. Limitation of Liability. Neither party is liable for ...",
      "matches": [{"start": 1838, "end": 1848}, {"start": 1852, "end": 1861}, {"start": 2016, "end": 2025}]
    }
  ]
}
```

- A hit is a clause-sized block: a paragraph, cut at a line break or a space if it runs past 1,000 characters.
- A block has to contain every query word, matched whole and without regard to case. There is no stopword list, because "shall" and "may" are different obligations. There is no stemming yet: "indemnify" does not find "indemnification".
- Ranking is BM25 over the blocks being searched, paged with `limit` and `offset`.
- `snippet` is exactly `text[start:end]` and `matches` are offsets at the `version` given, so a hit can be edited by position: `{"range": {"start": 1838, "end": 1848}, "text": "Limits"}` with `If-Match: "2"` replaces the first match above.

### Suggestions

`POST /documents/{id}/suggestions` with `{"instruction": "Make the payment terms Net 45"}` asks the model for quotes and replacements. Each quote goes through the same check as any other change: it has to appear exactly once and cannot overlap another suggestion. What passes comes back as changes ready to send to `PATCH`, with the version they were checked against. What fails comes back under `rejected` with the reason. Nothing is written.

So a quote the model made up cannot become an edit, and text in a contract that tries to instruct the model can at worst produce a suggestion that a person still has to apply. The call gets one attempt and a two-minute timeout, and a document over 200,000 characters is a 413. Past a prototype it would be a job with a status URL.

### Errors

Every error is `{ "error": "...", "code": 409 }`. 400, 415 and 422 mean fix the request; 409 and 412 mean look at the document again.

| Code | Meaning |
|---|---|
| 400, 415 | the body is not JSON, or was not sent as JSON |
| 404 | no such document |
| 409 | the change does not fit this document: text not found, ambiguous, no such occurrence, range outside the document, overlapping changes |
| 412 | `If-Match` is not the current version |
| 422 | the request is invalid |
| 428 | a change by position was sent without `If-Match` |
| 413, 502, 503 | suggestions: the document is too large, the model call failed, or no key is configured |

## The UI

One screen. The change form and the model fill the same pending change set. The preview is a dry run of the checked changes, drawn as a redline, and Apply sends exactly what was previewed, with `If-Match`. Selecting text in the document fills the form with that text and its occurrence number.

## Performance

`make bench` on an Apple M5 Max with Python 3.12. Best of three runs, engine only (no HTTP), on synthetic contracts.

| one document | 1 MB | 10 MB |
|---|---|---|
| one unique replace | 0.2 ms | 1.3 ms |
| replace every "Agreement" | 2.5 ms (4,526 matches) | 26.0 ms (44,994 matches) |
| change set of 20 distinct terms | 19.1 ms | 207.5 ms |
| build the index | 31.7 ms | 322.2 ms |
| search "agreement shall" | 0.8 ms (1,835 hits) | 8.2 ms (17,953 hits) |
| search "larkspur tamberlane delaware" | 0.1 ms (29 hits) | 0.4 ms (253 hits) |
| scan every block for "agreement shall" (no index) | 17.4 ms (1,835 hits) | 191.4 ms (17,953 hits) |

- Time grows with the text: ten times the size takes about ten times as long. Each change is one `str.find` scan and applying is one pass and one join, so k changes with m edits cost O(k·n) to find and O(n + m log m) to order and apply.
- Search uses an inverted index per document, cached by document version and never updated in place. An edit leaves it alone and the next search rebuilds it. That keeps a 10 MB edit at 26 ms where rebuilding on every write would add about 320, and the index has paid for itself by the second search of a version (8 ms against 190 ms for a scan).

The limit of this design is the number of documents. Each document keeps its own index, which makes an edit cheap, but every query visits every index and the first search after a restart builds all of them. With 10,000 documents of 20 KB in one process (the second table `make bench` prints), that first search takes 6.4 s, later ones take 27 to 196 ms, and the process holds over 2 GB. Past one machine the index belongs in Postgres full-text or OpenSearch, built when a document is uploaded ([INFRA.md](INFRA.md)).

The benchmark text comes from seven clause templates, so it has about a hundred distinct words. Real contracts have a larger vocabulary and larger indexes.

## Tests

`make test` runs both suites in a few seconds and prints coverage, which has to stay at 100% of lines and branches. Beyond the unit tests for each rule:

- Property tests (Hypothesis) check the core against simpler versions of itself: a random change set either is rejected or equals a naive regex-and-splice version, and indexed search returns exactly the blocks a brute-force scan finds.
- Concurrency: sixteen threads commit against the same version and exactly one wins.
- Large files: a 10 MB document, plus tests that time 1 MB against 8 MB and fail if the ratio looks quadratic.
- The API tests go through HTTP and cover every status code above. The model call is tested against a stub that fails the way the SDK does, including a cut-off answer.

## Left out

- Word files. Real redlines are tracked changes in .docx, and the hard part in production is mapping text offsets back to Word runs without disturbing the formatting.
- History: tracked changes that someone else accepts or rejects later, and undo.
- Tolerant matching in changes (curly quotes, runs of whitespace), and stemming, phrases and semantic search.
- Suggestions on large documents, which need the relevant clauses retrieved first.
- Limits and paging. Nothing caps the size of a body or a document, `GET /documents` returns the whole list, `PATCH` returns the whole updated text, and the UI renders the first 200,000 characters of a document.
- Auth, tenancy and persistence, which [INFRA.md](INFRA.md) covers.
