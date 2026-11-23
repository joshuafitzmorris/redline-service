import { useRef, useState, type ChangeEvent, type FormEvent } from "react";
import * as api from "./api";
import type { Hit, Summary } from "./api";
import { excerpt } from "./redline";
import Segments from "./Segments";

type Props = {
  docs: Summary[] | null;
  openId: string | undefined;
  busy: boolean;
  onOpen: (id: string, hit?: Hit) => void;
  onCreated: (id: string) => void;
  onError: (error: unknown) => void;
};

type Search = { query: string; docId?: string };

/** The document list, the new-document form and search. */
export default function Sidebar({ docs, openId, busy, onOpen, onCreated, onError }: Props) {
  const [adding, setAdding] = useState(false);
  const [found, setFound] = useState<{ search: Search; hits: Hit[]; total: number } | null>(null);
  // Responses can arrive out of order, so only those for the latest search are shown.
  const latest = useRef<Search>(null);
  const empty = docs?.length === 0;
  const more = found !== null && found.hits.length < found.total;
  const stale = (h: Hit) => docs?.some((d) => d.id === h.document_id && d.version !== h.version);

  // Nothing reads the search form until it is submitted, so its fields keep their own state.
  function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const docId = form.get("scope") === "doc" ? openId : undefined;
    latest.current = { query: String(form.get("query")), docId };
    load(latest.current, []);
  }

  function load(search: Search, shown: Hit[]) {
    api.search(search.query, { docId: search.docId, offset: shown.length }).then((page) => {
      if (search !== latest.current) return;
      setFound({ search, hits: [...shown, ...page.hits], total: page.total });
    }, onError);
  }

  function created(id: string) {
    setAdding(false);
    onCreated(id);
  }

  return (
    <aside className="sidebar" aria-label="Documents and search">
      <section>
        <h2>Documents</h2>
        <ul className="list">
          {docs?.map((doc) => (
            <li key={doc.id}>
              <button
                aria-current={doc.id === openId}
                disabled={busy}
                onClick={() => onOpen(doc.id)}
              >
                {doc.title}
              </button>
            </li>
          ))}
        </ul>
        {empty ? (
          <p className="muted">No documents yet</p>
        ) : (
          <button aria-expanded={adding} onClick={() => setAdding(!adding)}>
            New document
          </button>
        )}
        {(adding || empty) && <NewDocument busy={busy} onCreated={created} onError={onError} />}
      </section>

      <section>
        <form role="search" onSubmit={search}>
          <h2>
            <label htmlFor="query">Search</label>
          </h2>
          <input id="query" name="query" type="search" required />
          <fieldset className="scope" aria-label="Search in">
            <label>
              <input type="radio" name="scope" value="all" defaultChecked />
              All documents
            </label>
            <label>
              <input type="radio" name="scope" value="doc" disabled={!openId} />
              This document
            </label>
          </fieldset>
        </form>
        {found && (
          <p className="muted">
            {more && `${found.hits.length} of `}
            {found.total} {found.total === 1 ? "result" : "results"}
          </p>
        )}
        <ul className="list">
          {found?.hits.map((hit) => (
            <li key={`${hit.document_id}:${hit.start}`}>
              <button disabled={busy} onClick={() => onOpen(hit.document_id, hit)}>
                <strong>{hit.title}</strong>
                {stale(hit) && <span className="muted"> edited since</span>}
                <span className="snippet"><Segments segments={excerpt(hit)} /></span>
              </button>
            </li>
          ))}
        </ul>
        {more && <button onClick={() => load(found.search, found.hits)}>Show more</button>}
      </section>
    </aside>
  );
}

function NewDocument({ busy, onCreated, onError }: Pick<Props, "busy" | "onCreated" | "onError">) {
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [creating, setCreating] = useState(false);

  async function create(event: FormEvent) {
    event.preventDefault();
    if (creating) return;
    setCreating(true);
    try {
      onCreated((await api.createDocument(title, text)).id);
    } catch (error) {
      onError(error);
    } finally {
      setCreating(false);
    }
  }

  function load(event: ChangeEvent<HTMLInputElement>) {
    event.target.files?.[0]?.text().then(setText, onError);
  }

  return (
    <form className="new" onSubmit={create}>
      <label>
        Title
        <input required value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>
      <label>
        Text
        <textarea required rows={8} value={text} onChange={(e) => setText(e.target.value)} />
      </label>
      <label>
        Or read a .txt file
        <input type="file" accept=".txt,text/plain" onChange={load} />
      </label>
      <button disabled={busy}>Create</button>
    </form>
  );
}
