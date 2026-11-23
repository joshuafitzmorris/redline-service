// Client for the Redline HTTP API. Every offset it returns counts Unicode code points.

export type Target = { text: string; occurrence?: number | "all" };
export type Change = { operation?: "replace"; target: Target; replacement: string };
export type Edit = { change: number; start: number; removed: string; inserted: string };
export type Summary = { id: string; title: string; version: number; length: number };
export type Doc = { id: string; title: string; version: number; text: string };
export type EditResult = Doc & { committed: boolean; edits: Edit[] };
export type Span = { start: number; end: number };
export type Hit = {
  document_id: string;
  title: string;
  version: number;
  score: number;
  start: number;
  end: number;
  snippet: string;
  matches: Span[];
};
export type SearchResult = {
  query: string;
  total: number;
  limit: number;
  offset: number;
  hits: Hit[];
};
export type Suggestion = { change: Change; rationale: string };
export type Rejected = { quote: string; replacement: string; rationale: string; reason: string };
export type Suggestions = { version: number; suggestions: Suggestion[]; rejected: Rejected[] };

/** A failed request: the HTTP status and the server's message. */
export class ApiError extends Error {
  readonly code: number;

  constructor(code: number, message: string) {
    super(message);
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  // Every error the server sends is {error, code}; a proxy failure may not be JSON at all.
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = typeof body === "object" && body !== null && "error" in body ? body.error : null;
    const message = typeof error === "string" ? error : `${response.status} ${response.statusText}`;
    throw new ApiError(response.status, message);
  }
  return body as T;
}

const documentPath = (id: string) => `/documents/${encodeURIComponent(id)}`;

function send(method: string, body: unknown, headers?: Record<string, string>): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  };
}

export async function listDocuments(): Promise<Summary[]> {
  return (await request<{ documents: Summary[] }>("/documents")).documents;
}

export function getDocument(id: string): Promise<Doc> {
  return request(documentPath(id));
}

export function createDocument(title: string, text: string): Promise<Summary> {
  return request("/documents", send("POST", { title, text }));
}

/** Applies changes to the loaded version of doc, or with dryRun only shows what they would do. */
export function editDocument(
  doc: Doc,
  changes: Change[],
  { dryRun = false } = {},
): Promise<EditResult> {
  const path = `${documentPath(doc.id)}${dryRun ? "?dry_run=true" : ""}`;
  return request(path, send("PATCH", { changes }, { "If-Match": `"${doc.version}"` }));
}

type SearchOptions = { docId?: string; offset: number };

export function search(query: string, { docId, offset }: SearchOptions): Promise<SearchResult> {
  const params = new URLSearchParams({ q: query, offset: String(offset) });
  return request(`${docId ? documentPath(docId) : "/documents"}/search?${params}`);
}

export function suggest(id: string, instruction: string): Promise<Suggestions> {
  return request(`${documentPath(id)}/suggestions`, send("POST", { instruction }));
}
