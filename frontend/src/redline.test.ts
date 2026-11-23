import { expect, test } from "vitest";
import type { Edit, Hit, Span } from "./api";
import { excerpt, head, highlight, occurrenceAt, redline } from "./redline";

const edit = (start: number, removed: string, inserted: string, change = 0): Edit => ({
  change,
  start,
  removed,
  inserted,
});

const hit = (snippet: string, matches: Span[], start = 100): Hit => ({
  document_id: "msa",
  title: "MSA",
  version: 1,
  score: 1,
  start,
  end: start + Array.from(snippet).length,
  snippet,
  matches,
});

test("redline with no edits is the text unchanged", () => {
  expect(redline("Net 30 days", [])).toEqual([{ kind: "same", text: "Net 30 days" }]);
});

test("a replacement shows the removed text, then the inserted text", () => {
  expect(redline("pay within Net 30 days", [edit(11, "Net 30", "Net 45")])).toEqual([
    { kind: "same", text: "pay within " },
    { kind: "removed", text: "Net 30" },
    { kind: "inserted", text: "Net 45" },
    { kind: "same", text: " days" },
  ]);
});

test("a deletion shows only the removed text", () => {
  expect(redline("pay promptly within 30 days", [edit(4, "promptly ", "")])).toEqual([
    { kind: "same", text: "pay " },
    { kind: "removed", text: "promptly " },
    { kind: "same", text: "within 30 days" },
  ]);
});

test("two edits keep the text between them", () => {
  expect(redline("A and B", [edit(0, "A", "X"), edit(6, "B", "Y", 1)])).toEqual([
    { kind: "removed", text: "A" },
    { kind: "inserted", text: "X" },
    { kind: "same", text: " and " },
    { kind: "removed", text: "B" },
    { kind: "inserted", text: "Y" },
  ]);
});

test("offsets count code points, so an emoji before an edit does not shift it", () => {
  // The emoji is one code point but two UTF-16 units: "Net 30" starts at code point 2.
  expect(redline("😀 Net 30 days", [edit(2, "Net 30", "Net 45")])).toEqual([
    { kind: "same", text: "😀 " },
    { kind: "removed", text: "Net 30" },
    { kind: "inserted", text: "Net 45" },
    { kind: "same", text: " days" },
  ]);
});

test("an emoji inside removed text does not shift the edits after it", () => {
  // "a😀b" is three code points but four UTF-16 units, so "c" starts at code point 8.
  expect(redline("a😀b and c", [edit(0, "a😀b", "x"), edit(8, "c", "y", 1)])).toEqual([
    { kind: "removed", text: "a😀b" },
    { kind: "inserted", text: "x" },
    { kind: "same", text: " and " },
    { kind: "removed", text: "c" },
    { kind: "inserted", text: "y" },
  ]);
});

test("highlight subtracts the base offset from each span", () => {
  const snippet = "7. Limitation of Liability.";
  const matches = [
    { start: 103, end: 113 },
    { start: 117, end: 126 },
  ];
  expect(highlight(snippet, matches, 100)).toEqual([
    { kind: "same", text: "7. " },
    { kind: "focus", text: "Limitation" },
    { kind: "same", text: " of " },
    { kind: "focus", text: "Liability" },
    { kind: "same", text: "." },
  ]);
});

test("highlight counts code points, so an emoji in the snippet does not shift a match", () => {
  expect(highlight("😀 Net 30", [{ start: 102, end: 105 }], 100)).toEqual([
    { kind: "same", text: "😀 " },
    { kind: "focus", text: "Net" },
    { kind: "same", text: " 30" },
  ]);
});

test("head counts code points and never splits an emoji", () => {
  expect(head("😀😀😀", 2)).toBe("😀😀");
});

test("head returns text under the limit as it is", () => {
  expect(head("Net 30", 10)).toBe("Net 30");
});

test("occurrenceAt finds the only match", () => {
  expect(occurrenceAt("pay within Net 30 days", "Net 30", 11)).toEqual({ n: 1, of: 1 });
});

test("occurrenceAt numbers a match among several", () => {
  expect(occurrenceAt("Net 30, Net 30, Net 30", "Net 30", 8)).toEqual({ n: 2, of: 3 });
});

test("occurrenceAt is null where no match starts", () => {
  expect(occurrenceAt("pay within Net 30 days", "Net 30", 12)).toBeNull();
});

test("occurrenceAt does not count overlapping matches, like the server", () => {
  expect(occurrenceAt("aaaa", "aa", 0)).toEqual({ n: 1, of: 2 });
  expect(occurrenceAt("aaaa", "aa", 1)).toBeNull();
  expect(occurrenceAt("aaaa", "aa", 2)).toEqual({ n: 2, of: 2 });
});

test("excerpt leaves a snippet whole when its first match is near the start", () => {
  expect(excerpt(hit("Payment is due within Net 30 days.", [{ start: 122, end: 125 }]))).toEqual([
    { kind: "same", text: "Payment is due within " },
    { kind: "focus", text: "Net" },
    { kind: "same", text: " 30 days." },
  ]);
});

test("excerpt cuts a long lead-in to an ellipsis and the last words before the match", () => {
  // The last 12 code points before the match are "e four five ", which starts mid-word.
  expect(excerpt(hit("one two three four five Net 30", [{ start: 124, end: 127 }]), 12)).toEqual([
    { kind: "same", text: "…four five " },
    { kind: "focus", text: "Net" },
    { kind: "same", text: " 30" },
  ]);
});

test("excerpt counts code points, so an emoji before the match does not shift the marks", () => {
  const snippet = "one two three 😀 four five Net 30";
  expect(excerpt(hit(snippet, [{ start: 126, end: 129 }]), 13)).toEqual([
    { kind: "same", text: "…😀 four five " },
    { kind: "focus", text: "Net" },
    { kind: "same", text: " 30" },
  ]);
});
