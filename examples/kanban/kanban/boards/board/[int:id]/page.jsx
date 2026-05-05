import ReactDOM from "react-dom/client";
import { useState } from "react";
import { Column } from "./_pieces/column/component";

function applyMoveLocally(columns, cardId, targetColumnId, targetPosition) {
  const id = Number(cardId);
  const targetId = Number(targetColumnId);
  let moved = null;
  const stripped = columns.map((col) => {
    const idx = col.cards.findIndex((c) => c.id === id);
    if (idx === -1) return col;
    moved = col.cards[idx];
    const next = col.cards.slice();
    next.splice(idx, 1);
    return {
      ...col,
      cards: next.map((c, i) => ({ ...c, position: i })),
    };
  });
  if (moved === null) return columns;
  return stripped.map((col) => {
    if (col.id !== targetId) return col;
    const insertAt = Math.min(targetPosition, col.cards.length);
    const next = col.cards.slice();
    next.splice(insertAt, 0, moved);
    return {
      ...col,
      cards: next.map((c, i) => ({ ...c, position: i })),
    };
  });
}

export function Board() {
  const ctx = window.Next?.context?.board ?? {};
  const [columns, setColumns] = useState(ctx.columns ?? []);
  const [errorMsg, setErrorMsg] = useState(null);

  async function moveCard(cardId, targetColumnId, targetPosition) {
    if (!ctx.move_card_url) return;
    const prev = columns;
    setColumns(applyMoveLocally(prev, cardId, targetColumnId, targetPosition));
    setErrorMsg(null);
    try {
      const response = await fetch(ctx.move_card_url, {
        method: "POST",
        headers: { "X-CSRFToken": ctx.csrf ?? "" },
        body: new URLSearchParams({
          card_id: cardId,
          target_column_id: targetColumnId,
          target_position: String(targetPosition),
          csrfmiddlewaretoken: ctx.csrf ?? "",
        }),
      });
      if (!response.ok) {
        setColumns(prev);
        setErrorMsg("Move rejected by server.");
      }
    } catch {
      setColumns(prev);
      setErrorMsg("Network error. The move was rolled back.");
    }
  }

  return (
    <div className="space-y-3">
      {errorMsg && (
        <div
          role="alert"
          data-kanban-error
          className="flex items-center justify-between rounded-md border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800"
        >
          <span>{errorMsg}</span>
          <button
            type="button"
            onClick={() => setErrorMsg(null)}
            className="ml-3 rounded px-2 text-rose-600 hover:bg-rose-100"
            aria-label="Dismiss"
          >
            ×
          </button>
        </div>
      )}
      <div className="flex gap-4 overflow-x-auto pb-4">
        {columns.map((col) => (
          <Column key={col.id} column={col} onDrop={moveCard} />
        ))}
      </div>
    </div>
  );
}

const el = document.getElementById("kanban-board");
if (el) ReactDOM.createRoot(el).render(<Board />);
