import { useRef, useState, type FormEvent, type RefObject } from "react";
import type { Change, Edit, Rejected } from "./api";
import type { Occurrence } from "./redline";

/** A change waiting to be applied, typed in the form or suggested by the model. */
export type Pending = { change: Change; rationale?: string; checked: boolean };

/** Text selected in the document, to load into the form. `key` is new for every selection. */
export type Picked = { key: number; text: string; occurrence: Occurrence | null };

type Match = "one" | "nth" | "all";

type Props = {
  pending: Pending[];
  checked: Change[];
  rejected: Rejected[];
  edits: Edit[] | null;
  status: string;
  picked: Picked | null;
  onPending: (pending: Pending[], rejected: Rejected[]) => void;
  onAsk: (instruction: string) => void;
  onApply: () => Promise<boolean>;
};

/** The change form and the model's suggestions, which both fill one pending change set. */
export default function Changes(props: Props) {
  const { pending, checked, rejected, edits, status, picked } = props;
  const { onPending, onAsk, onApply } = props;
  const [instruction, setInstruction] = useState("");
  const find = useRef<HTMLInputElement>(null);

  function add(change: Change) {
    onPending([...pending, { change, checked: true }], rejected);
  }

  function ask(event: FormEvent) {
    event.preventDefault();
    onAsk(instruction);
  }

  function toggle(index: number) {
    const next = pending.map((p, i) => (i === index ? { ...p, checked: !p.checked } : p));
    onPending(next, rejected);
  }

  // Apply and Discard remove their own buttons, so focus moves to where a new change starts.
  async function apply() {
    if (await onApply()) find.current?.focus();
  }

  function discard() {
    onPending([], []);
    find.current?.focus();
  }

  return (
    <aside className="changes" aria-label="Changes">
      <p className="status muted" role="status">{status}</p>
      <ChangeForm key={picked?.key} picked={picked} findRef={find} onAdd={add} />
      <form onSubmit={ask}>
        <h2>
          <label htmlFor="instruction">Suggest</label>
        </h2>
        <textarea
          id="instruction"
          required
          maxLength={2000}
          rows={3}
          placeholder="Make the payment terms Net 45"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
        />
        <button>Ask the model</button>
      </form>
      {pending.length > 0 && (
        <section>
          <h2>Pending changes</h2>
          <ul className="items">
            {pending.map((item, i) => {
              const { text, occurrence } = item.change.target;
              const index = checked.indexOf(item.change);
              const count = edits?.filter((edit) => edit.change === index).length ?? 0;
              const which = occurrence === "all" ? "every one" : occurrence && `number ${occurrence}`;
              return (
                <li key={i}>
                  <label>
                    <input type="checkbox" checked={item.checked} onChange={() => toggle(i)} />
                    <span>
                      <del>{text}</del> → <ins>{item.change.replacement}</ins>
                      <span className="muted">
                        {which && ` ${which}`}
                        {count > 1 && ` (${count} edits)`}
                      </span>
                    </span>
                  </label>
                  {item.rationale && <p className="muted">{item.rationale}</p>}
                </li>
              );
            })}
          </ul>
          <button disabled={!edits} onClick={apply}>
            Apply {checked.length} {checked.length === 1 ? "change" : "changes"}
          </button>{" "}
          <button onClick={discard}>Discard</button>
        </section>
      )}
      {rejected.length > 0 && (
        <section>
          <h2>Rejected</h2>
          <ul className="items muted">
            {rejected.map((proposal, i) => (
              <li key={i}>
                {proposal.quote} → {proposal.replacement}
                <p>{proposal.reason}</p>
              </li>
            ))}
          </ul>
        </section>
      )}
    </aside>
  );
}

type FormProps = {
  picked: Picked | null;
  findRef: RefObject<HTMLInputElement | null>;
  onAdd: (change: Change) => void;
};

/** The change form. It starts from `picked`, so a new key loads a new selection into it. */
function ChangeForm({ picked, findRef, onAdd }: FormProps) {
  const n = picked?.occurrence && picked.occurrence.of > 1 ? picked.occurrence.n : null;
  const [find, setFind] = useState(picked?.text ?? "");
  const [replacement, setReplacement] = useState("");
  const [match, setMatch] = useState<Match>(n === null ? "one" : "nth");
  const [nth, setNth] = useState(String(n ?? 2));

  function add(event: FormEvent) {
    event.preventDefault();
    const occurrence = match === "nth" ? Number(nth) : match === "all" ? "all" : undefined;
    onAdd({ target: { text: find, occurrence }, replacement });
    setFind("");
    setReplacement("");
    setMatch("one");
  }

  const option = (value: Match, label: string) => (
    <label>
      <input type="radio" name="match" checked={match === value} onChange={() => setMatch(value)} />
      {label}
    </label>
  );

  return (
    <form onSubmit={add}>
      <h2>Change</h2>
      <label className="field">
        Find
        <input ref={findRef} required value={find} onChange={(e) => setFind(e.target.value)} />
      </label>
      <label className="field">
        Replace with
        <input value={replacement} onChange={(e) => setReplacement(e.target.value)} />
      </label>
      <fieldset className="field">
        <legend>Match</legend>
        {option("one", "the only one")}
        <span>
          {option("nth", "number")}
          <input
            type="number"
            aria-label="Occurrence number"
            min={1}
            required
            disabled={match !== "nth"}
            value={nth}
            onChange={(e) => setNth(e.target.value)}
          />
        </span>
        {option("all", "every one")}
      </fieldset>
      <button>Add change</button>
    </form>
  );
}
