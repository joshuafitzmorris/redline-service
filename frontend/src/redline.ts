import type { Edit, Hit, Span } from "./api";

// API offsets count code points, but JavaScript strings index UTF-16 units, two to an emoji. This
// module is the one place that converts: an API offset only ever indexes a string's code points.

export type Segment = { kind: "same" | "removed" | "inserted" | "focus"; text: string };
export type Occurrence = { n: number; of: number };

/** The base text with each edit shown as removed then inserted. Edits come in document order. */
export function redline(text: string, edits: Edit[]): Segment[] {
  const chars = Array.from(text);
  const segments: Segment[] = [];
  let at = 0;
  for (const edit of edits) {
    add(segments, "same", chars.slice(at, edit.start).join(""));
    add(segments, "removed", edit.removed);
    add(segments, "inserted", edit.inserted);
    at = edit.start + Array.from(edit.removed).length;
  }
  add(segments, "same", chars.slice(at).join(""));
  return segments;
}

/** Text with spans marked "focus"; `base` is subtracted from each span, for snippets. */
export function highlight(text: string, spans: Span[], base = 0): Segment[] {
  const chars = Array.from(text);
  const segments: Segment[] = [];
  let at = 0;
  for (const span of spans) {
    add(segments, "same", chars.slice(at, span.start - base).join(""));
    add(segments, "focus", chars.slice(span.start - base, span.end - base).join(""));
    at = span.end - base;
  }
  add(segments, "same", chars.slice(at).join(""));
  return segments;
}

/** A hit's snippet for the result list: matches marked, and cut to start near the first one. */
export function excerpt(hit: Hit, lead = 60): Segment[] {
  const segments = highlight(hit.snippet, hit.matches, hit.start);
  const [first, ...rest] = segments;
  const before = hit.matches.length > 0 && first.kind === "same" ? Array.from(first.text) : [];
  if (before.length <= lead) return segments;
  const tail = before.slice(-lead).join("");
  // Start after the tail's first whitespace, if any, so as not to open in the middle of a word.
  return [{ kind: "same", text: `…${tail.slice(tail.search(/\s/) + 1)}` }, ...rest];
}

/** The first `limit` code points of text. */
export function head(text: string, limit: number): string {
  // A string has at least as many UTF-16 units as code points, so a short one is already whole.
  if (text.length <= limit) return text;
  let end = 0;
  let count = 0;
  for (const char of text) {
    if (count++ === limit) break;
    end += char.length;
  }
  return text.slice(0, end);
}

/** Which non-overlapping match of needle starts at `at` (1-based), and how many there are. */
export function occurrenceAt(text: string, needle: string, at: number): Occurrence | null {
  // The server's scan, so the numbers agree with how it resolves a change. Unlike API offsets,
  // `at` counts UTF-16 units, as the DOM does; a match's number is the same in either unit.
  if (!needle) return null;
  let n = 0;
  let of = 0;
  for (let i = text.indexOf(needle); i !== -1; i = text.indexOf(needle, i + needle.length)) {
    of++;
    if (i === at) n = of;
  }
  return n > 0 ? { n, of } : null;
}

function add(segments: Segment[], kind: Segment["kind"], text: string): void {
  if (text) segments.push({ kind, text });
}
