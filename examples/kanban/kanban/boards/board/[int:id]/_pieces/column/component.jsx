import { useState } from "react";
import { Card } from "../card/component";

export function Column({ column, onDrop, onCreate }) {
  const [dropActive, setDropActive] = useState(false);
  const [draft, setDraft] = useState("");
  const overLimit = column.wip_limit != null && column.cards.length > column.wip_limit;

  return (
    <div
      data-kanban-column={column.id}
      onDragOver={(e) => {
        e.preventDefault();
        setDropActive(true);
      }}
      onDragLeave={() => setDropActive(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDropActive(false);
        const cardId = e.dataTransfer.getData("text/card-id");
        onDrop(cardId, column.id, column.cards.length);
      }}
      className={`kanban-column flex flex-col gap-2 rounded-lg border border-slate-200 bg-slate-50 p-3 w-64${dropActive ? " drop-active ring-2 ring-blue-400" : ""}`}
    >
      <header className="flex items-center justify-between gap-2 px-1">
        <h3 className="text-sm font-semibold text-slate-700">{column.title}</h3>
        {column.wip_limit != null && (
          <span
            data-kanban-wip
            className={`rounded px-2 py-0.5 text-xs ${
              overLimit ? "bg-rose-200 text-rose-800" : "bg-slate-200 text-slate-700"
            }`}
          >
            {column.cards.length}/{column.wip_limit}
          </span>
        )}
      </header>
      <div className="flex flex-col gap-2">
        {column.cards.map((card) => (
          <Card key={card.id} {...card} />
        ))}
      </div>
      {/* mt-1 lands on the same gap the server markup gets from mt-3, because
          the React column already spaces its children with gap-2. */}
      <form
        className="mt-1 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          const title = draft.trim();
          if (!title) return;
          setDraft("");
          onCreate?.(column.id, title);
        }}
      >
        <input
          type="text"
          name="title"
          value={draft}
          placeholder="New card"
          aria-label={`New card in ${column.title}`}
          onChange={(event) => setDraft(event.target.value)}
          className="flex-1 rounded border border-slate-300 bg-white px-2 py-1 text-xs"
        />
        <button
          type="submit"
          className="rounded bg-slate-900 px-2 py-1 text-xs font-medium text-white"
        >
          Add
        </button>
      </form>
    </div>
  );
}
