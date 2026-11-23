import { useEffect, useMemo, useRef } from "react";
import type { Doc, Edit, Span } from "./api";
import { head, highlight, occurrenceAt, redline, type Occurrence } from "./redline";
import Segments from "./Segments";

// Rendering more text than this makes the page sluggish.
const LIMIT = 200_000;

type Props = {
  doc: Doc;
  edits: Edit[] | null;
  focus: Span[];
  onSelect: (text: string, occurrence: Occurrence | null) => void;
};

/** The open document, shown as a redline while a preview is active. */
export default function Viewer({ doc, edits, focus, onSelect }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const shown = useMemo(() => head(doc.text, LIMIT), [doc.text]);
  // Offsets past the cut have nothing to point at, so those edits and spans are left out.
  const segments = useMemo(() => {
    if (edits) return redline(shown, edits.filter((edit) => edit.start < LIMIT));
    return highlight(shown, focus.filter((span) => span.start < LIMIT));
  }, [shown, edits, focus]);

  useEffect(() => {
    container.current?.querySelector("del, ins, mark")?.scrollIntoView({ block: "center" });
  }, [edits, focus]);

  // Selecting text fills the change form with that text and its occurrence number.
  function select() {
    const box = container.current;
    const selection = getSelection();
    if (!box || !selection?.rangeCount) return;
    const range = selection.getRangeAt(0);
    const text = documentText(range);
    if (!text.trim() || !box.contains(range.commonAncestorContainer)) return;
    const before = document.createRange();
    before.setStart(box, 0);
    before.setEnd(range.startContainer, range.startOffset);
    onSelect(text, occurrenceAt(doc.text, text, documentText(before).length));
  }

  return (
    <main className="viewer">
      <header>
        <h1>{doc.title}</h1>
        <span className="muted">v{doc.version}</span>
      </header>
      {shown.length < doc.text.length && (
        <p className="notice">Only the first 200,000 characters of this document are shown.</p>
      )}
      <div className="text" ref={container} onMouseUp={select}>
        <Segments segments={segments} />
      </div>
    </main>
  );
}

/** The document text a range covers: what is on screen without the insertions of a preview. */
function documentText(range: Range): string {
  // Inside an insertion there is no document text. A range that only starts or ends in one is
  // cloned with the <ins> element around that part, which the removal below takes out.
  const node = range.commonAncestorContainer;
  if ((node instanceof Element ? node : node.parentElement)?.closest("ins")) return "";
  const copy = range.cloneContents();
  copy.querySelectorAll("ins").forEach((inserted) => inserted.remove());
  return copy.textContent ?? "";
}
