import ReactDOM from "react-dom/client";
import { useRef, useState } from "react";
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

function appendCardLocally(columns, columnId, card) {
  const targetId = Number(columnId);
  return columns.map((col) =>
    col.id === targetId
      ? { ...col, cards: [...col.cards, { ...card, position: col.cards.length }] }
      : col,
  );
}

function dropCardLocally(columns, cardId) {
  return columns.map((col) =>
    col.cards.some((card) => card.id === cardId)
      ? {
          ...col,
          cards: col.cards
            .filter((card) => card.id !== cardId)
            .map((card, index) => ({ ...card, position: index })),
        }
      : col,
  );
}

function namePendingCard(columns, pendingId, cardId) {
  return columns.map((col) => ({
    ...col,
    cards: col.cards.map((card) =>
      card.id === pendingId ? { ...card, id: cardId, pending: false } : card,
    ),
  }));
}

// The create action answers with a redirect that carries the new row id, so the
// followed response URL is where the client learns which card it just drew.
function readCreatedId(responseUrl) {
  if (!responseUrl) return null;
  const raw = new URL(responseUrl, window.location.href).searchParams.get("created");
  const id = Number(raw);
  return raw && Number.isInteger(id) ? id : null;
}

export function Board() {
  const ctx = window.Next?.context?.board ?? {};
  const [columns, setColumns] = useState(ctx.columns ?? []);
  const [errorMsg, setErrorMsg] = useState(null);
  const pendingSeq = useRef(0);

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

  async function createCard(columnId, title) {
    const text = title.trim();
    if (!ctx.create_card_url || !text) return;
    const pendingId = `pending-${(pendingSeq.current += 1)}`;
    setColumns((cols) =>
      appendCardLocally(cols, columnId, {
        id: pendingId,
        title: text,
        excerpt: "",
        pending: true,
      }),
    );
    setErrorMsg(null);
    try {
      const response = await fetch(ctx.create_card_url, {
        method: "POST",
        headers: { "X-CSRFToken": ctx.csrf ?? "" },
        body: new URLSearchParams({
          column_id: String(columnId),
          title: text,
          csrfmiddlewaretoken: ctx.csrf ?? "",
        }),
      });
      const cardId = response.ok ? readCreatedId(response.url) : null;
      // Dropping the one pending card rather than restoring a snapshot keeps
      // a move that landed while the post was in flight.
      if (cardId === null) {
        setColumns((cols) => dropCardLocally(cols, pendingId));
        setErrorMsg("Card rejected by server.");
        return;
      }
      setColumns((cols) => namePendingCard(cols, pendingId, cardId));
    } catch {
      setColumns((cols) => dropCardLocally(cols, pendingId));
      setErrorMsg("Network error. The card was rolled back.");
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
          <Column key={col.id} column={col} onDrop={moveCard} onCreate={createCard} />
        ))}
      </div>
    </div>
  );
}

// A hot update re-evaluates this module, so the registry lives on window to
// outlive it and React Refresh keeps updating Board inside the existing root.
const roots = (window.__kanbanRoots ??= new WeakMap());

// onMount re-runs over an element a morph reconciled in place, so the
// WeakMap guard keeps the mount idempotent.
window.Next?.partial?.onMount("#kanban-board", (el) => {
  if (roots.has(el)) return;
  const root = ReactDOM.createRoot(el);
  root.render(<Board />);
  roots.set(el, root);
});

// next:removed fires on the detached root, not on each descendant, so the
// handler checks the node itself and walks its subtree for the board.
document.addEventListener("next:removed", (event) => {
  const node = event.target;
  if (!(node instanceof Element)) return;
  const islands = node.matches("#kanban-board")
    ? [node]
    : node.querySelectorAll("#kanban-board");
  for (const el of islands) {
    const root = roots.get(el);
    if (root !== undefined) {
      root.unmount();
      roots.delete(el);
    }
  }
});
