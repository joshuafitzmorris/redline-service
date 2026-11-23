#!/usr/bin/env bash
# A walk through the API with curl and jq. Start the server first (make api), then run this.
set -euo pipefail

BASE=${BASE:-http://localhost:8000}
HERE=$(cd "$(dirname "$0")" && pwd)
BODY=$(mktemp)
trap 'rm -f "$BODY"' EXIT

step() { printf '\n== %s\n' "$1"; }

# api METHOD PATH [curl options]: prints the status code and leaves the response body in $BODY.
api() {
  local method=$1 path=$2
  shift 2
  curl -sS -o "$BODY" -w 'HTTP %{http_code}\n' -X "$method" "$BASE$path" \
    -H 'Content-Type: application/json' "$@"
}

step "Create the license agreement from license-agreement.txt (201)"
jq -Rs '{title: "License agreement (curl walkthrough)", text: .}' "$HERE/license-agreement.txt" \
  | api POST /documents -d @-
jq . "$BODY"
ID=$(jq -r .id "$BODY")

step "Fetch it: the ETag is its version (200)"
api GET "/documents/$ID" -w 'HTTP %{http_code}, ETag: %header{etag}\n'
jq '{id, title, version, text: (.text[0:80] + "...")}' "$BODY"

step "Search every document for: contract (200)"
api GET "/documents/search?q=contract&limit=10&offset=0"
jq '{total, hits: [.hits[] | {title, score, snippet: (.snippet[0:60] + "...")}]}' "$BODY"

step "Search only this document for: limitation liability (200)"
api GET "/documents/$ID/search?q=limitation+liability"
jq '{total, hits: [.hits[] | {score, start, end, snippet, matches}]}' "$BODY"

step 'Replace "Net 30" without saying which one: it appears three times (409)'
api PATCH "/documents/$ID" \
  -d '{"changes": [{"target": {"text": "Net 30"}, "replacement": "Net 45"}]}'
jq . "$BODY"

step 'Replace only the second "Net 30" (200)'
api PATCH "/documents/$ID" \
  -d '{"changes": [{"target": {"text": "Net 30", "occurrence": 2}, "replacement": "Net 45"}]}'
jq '{version, committed, edits}' "$BODY"
VERSION=$(jq -r .version "$BODY")

CHANGES='{"changes": [
  {"target": {"text": "Licensee", "occurrence": "all"}, "replacement": "Customer"},
  {"target": {"text": "thirty (30) days"}, "replacement": "forty-five (45) days"}
]}'

step "Preview a bulk change set with dry_run=true: nothing is stored (200)"
api PATCH "/documents/$ID?dry_run=true" -H "If-Match: \"$VERSION\"" -d "$CHANGES"
jq '{version, committed, edits: (.edits | length), first: .edits[0]}' "$BODY"

step "Apply it with If-Match (200)"
api PATCH "/documents/$ID" -H "If-Match: \"$VERSION\"" -d "$CHANGES"
jq '{version, committed, edits: (.edits | length)}' "$BODY"

step "Apply it again with the old If-Match: the document has moved on (412)"
api PATCH "/documents/$ID" -H "If-Match: \"$VERSION\"" -d "$CHANGES"
jq . "$BODY"

step "Find Delaware in this document, to change it by position (200)"
api GET "/documents/$ID/search?q=delaware"
jq '{total, version: .hits[0].version, first_match: .hits[0].matches[0]}' "$BODY"
AT=$(jq -r .hits[0].version "$BODY")
RANGE=$(jq '{changes: [{operation: "replace", range: .hits[0].matches[0], text: "Maryland"}]}' "$BODY")

step "Replace that range, with the version the offsets came from in If-Match (200)"
api PATCH "/documents/$ID" -H "If-Match: \"$AT\"" -d "$RANGE"
jq '{version, committed, edits}' "$BODY"

step "Send the same range without If-Match: an offset needs a version (428)"
api PATCH "/documents/$ID" -d "$RANGE"
jq . "$BODY"

step 'Misspell "occurrence": unknown fields are rejected, not ignored (422)'
api PATCH "/documents/$ID" \
  -d '{"changes": [{"target": {"text": "Net 45", "occurence": 1}, "replacement": "Net 60"}]}'
jq . "$BODY"

step "Ask the model for suggestions (503 unless the server has ANTHROPIC_API_KEY)"
api POST "/documents/$ID/suggestions" -d '{"instruction": "Make the payment terms Net 45"}'
jq . "$BODY"
