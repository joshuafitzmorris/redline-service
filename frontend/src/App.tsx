import { useEffect, useEffectEvent, useMemo, useState } from "react";
import * as api from "./api";
import type { Doc, Edit, Hit, Rejected, Span, Summary } from "./api";
import Changes, { type Pending, type Picked } from "./Changes";
import Sidebar from "./Sidebar";
import Viewer from "./Viewer";

/** A dry run's outcome and what it was run on. `edits` is null when the dry run failed. */
type Preview = { doc: Doc; pending: Pending[]; edits: Edit[] | null };
type Working = "opening" | "applying" | "asking";

export default function App() {
  const [docs, setDocs] = useState<Summary[] | null>(null);
  const [doc, setDoc] = useState<Doc | null>(null);
  const [focus, setFocus] = useState<Span[]>([]);
  const [pending, setPending] = useState<Pending[]>([]);
  const [rejected, setRejected] = useState<Rejected[]>([]);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [picked, setPicked] = useState<Picked | null>(null);
  const [applied, setApplied] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const [working, setWorking] = useState<Working | null>(null);

  const fail = (e: unknown) => setError(e instanceof Error ? e : new Error(String(e)));
  const refresh = () => api.listDocuments().then(setDocs, fail);

  // Only checked changes are sent, and an edit names its change by its index in this list.
  const checked = useMemo(() => pending.filter((p) => p.checked).map((p) => p.change), [pending]);
  // A preview counts only for the document and changes it ran on, so it shows what Apply sends.
  const current = preview?.doc === doc && preview.pending === pending ? preview : null;
  const edits = current?.edits ?? null;
  const previewing = checked.length > 0 && current === null;

  // The form and the model both fill the one change set that preview and apply read. A new set
  // also retires the last error and the last apply message, which were about what came before.
  function fill(next: Pending[], nextRejected: Rejected[] = []) {
    setPending(next);
    setRejected(nextRejected);
    setError(null);
    setApplied("");
  }

  // One request that changes what is on screen runs at a time, so its response lands on the
  // document it was asked about. A failure goes to the banner; the result says which it was.
  async function run(what: Working, request: () => Promise<void>): Promise<boolean> {
    if (working) return false;
    setWorking(what);
    try {
      await request();
      return true;
    } catch (e) {
      fail(e);
      return false;
    } finally {
      setWorking(null);
    }
  }

  const open = (id: string, hit?: Hit) =>
    run("opening", async () => {
      const next = await api.getDocument(id);
      // Changes target text, so reopening the open document (Reload, a search hit) keeps them.
      if (next.id !== doc?.id) fill([]);
      setError(null);
      setApplied("");
      setDoc(next);
      // A hit's offsets hold only for the version it was found in.
      setFocus(hit?.version === next.version ? hit.matches : []);
    });

  const apply = () =>
    run("applying", async () => {
      if (!doc) return;
      const { id, title, version, text } = await api.editDocument(doc, checked);
      setDoc({ id, title, version, text });
      setFocus([]);
      fill([]);
      setApplied(`Applied. Now at version ${version}.`);
      refresh();
    });

  const ask = (instruction: string) =>
    run("asking", async () => {
      if (!doc) return;
      const result = await api.suggest(doc.id, instruction);
      fill(result.suggestions.map((s) => ({ ...s, checked: true })), result.rejected);
    });

  // `open` reads the current state, so the first load calls it through an effect event and the
  // effect below can run once, on mount.
  const loaded = useEffectEvent((list: Summary[]) => {
    setDocs(list);
    if (list.length > 0) open(list[0].id);
  });

  useEffect(() => {
    let live = true;
    api.listDocuments().then((list) => live && loaded(list), (e) => live && fail(e));
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    if (!doc || checked.length === 0) return;
    let live = true;
    api.editDocument(doc, checked, { dryRun: true }).then(
      (result) => live && setPreview({ doc, pending, edits: result.edits }),
      (e) => {
        if (!live) return;
        setPreview({ doc, pending, edits: null });
        fail(e);
      },
    );
    return () => {
      live = false;
    };
  }, [doc, pending, checked]);

  const select = (text: string, occurrence: Picked["occurrence"]) =>
    setPicked((last) => ({ key: (last?.key ?? 0) + 1, text, occurrence }));
  const status = working === "asking" ? "Asking the model…" : previewing ? "Previewing…" : applied;

  return (
    <div className="app">
      {error && (
        <div className="banner" role="alert">
          <span>{error.message}</span>
          {error instanceof api.ApiError && error.code === 412 && doc && (
            <button onClick={() => open(doc.id)}>Reload</button>
          )}
          <button onClick={() => setError(null)}>Dismiss</button>
        </div>
      )}
      <Sidebar
        docs={docs}
        openId={doc?.id}
        busy={working !== null}
        onOpen={open}
        onCreated={(id) => {
          refresh();
          open(id);
        }}
        onError={fail}
      />
      {doc && (
        <>
          <Viewer key={doc.id} doc={doc} edits={edits} focus={focus} onSelect={select} />
          <Changes
            pending={pending}
            checked={checked}
            rejected={rejected}
            edits={edits}
            status={status}
            picked={picked}
            onPending={fill}
            onAsk={ask}
            onApply={apply}
          />
        </>
      )}
    </div>
  );
}
