import ReactDOM from "react-dom/client";
import { useEffect, useRef, useState } from "react";
import { Column } from "./_components/column/component";
import { Presence } from "./_components/presence/component";

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
  const known = columns.some((col) => col.cards.some((card) => card.id === cardId));
  if (known) return dropCardLocally(columns, pendingId);
  return columns.map((col) => ({
    ...col,
    cards: col.cards.map((card) =>
      card.id === pendingId ? { ...card, id: cardId, pending: false } : card,
    ),
  }));
}

function withPendingCards(serverColumns, localColumns) {
  return serverColumns.map((col) => {
    const local = localColumns.find((candidate) => candidate.id === col.id);
    const pending = (local?.cards ?? []).filter((card) => card.pending);
    if (pending.length === 0) return col;
    const cards = [...col.cards, ...pending];
    return {
      ...col,
      cards: cards.map((card, index) => ({ ...card, position: index })),
    };
  });
}

function withLocalChanges(serverCols, localCols, moves) {
  let cols = withPendingCards(serverCols, localCols);
  for (const [cardId, move] of moves) {
    cols = applyMoveLocally(cols, cardId, move.column, move.position);
  }
  return cols;
}

function trackDragging(current, { card, active, by }) {
  const next = { ...current };
  if (active) next[card] = by;
  else delete next[card];
  return next;
}

const sameMember = (a, b) =>
  a.key === b.key && a.name === b.name && a.initials === b.initials;

function keepPresentDraggers(current, members) {
  const kept = Object.entries(current).filter(([, by]) =>
    members.some((member) => sameMember(member, by)),
  );
  return kept.length === Object.keys(current).length
    ? current
    : Object.fromEntries(kept);
}

// The create action answers with a redirect that carries the new row id, so the
// followed response URL is where the client learns which card it just drew.
function readCreatedId(responseUrl) {
  if (!responseUrl) return null;
  const raw = new URL(responseUrl, window.location.href).searchParams.get("created");
  const id = Number(raw);
  return raw && Number.isInteger(id) ? id : null;
}

const MOVE_FAILED = {
  invalid: "Move rejected by server.",
  error: "Network error. The move was rolled back.",
};

const CREATE_FAILED = {
  invalid: "Card rejected by server.",
  error: "Network error. The card was rolled back.",
};

export function Board() {
  const ctx = window.Next?.context?.board ?? {};
  const topic = ctx.topic;
  const [columns, setColumns] = useState(ctx.columns ?? []);
  const [errorMsg, setErrorMsg] = useState(null);
  const [presence, setPresence] = useState({ count: 0, members: [] });
  const [dragging, setDragging] = useState({});
  const pendingSeq = useRef(0);
  const serverColumns = useRef(ctx.columns ?? []);
  const movesInFlight = useRef(new Map());

  useEffect(() => {
    const next = window.Next;
    const offs = [
      next?.on?.("context-updated", ({ context, changed }) => {
        const fresh = context.board?.columns;
        if (!changed.includes("board") || !fresh) return;
        serverColumns.current = fresh;
        setColumns((cols) => withLocalChanges(fresh, cols, movesInFlight.current));
      }),
    ];
    const stream = next?.partial?.stream;
    if (topic && stream) {
      offs.push(
        stream.on(topic, "next:presence", ({ count, members = [] }) => {
          setPresence({ count, members });
          setDragging((current) => keepPresentDraggers(current, members));
        }),
        stream.on(topic, "board:dragging", (detail) => {
          if (detail.from === stream.meta()?.connection) return;
          setDragging((current) => trackDragging(current, detail));
        }),
      );
    }
    return () => {
      for (const off of offs) off?.();
    };
  }, [topic]);

  function announceDrag(cardId, active) {
    if (!topic) return;
    window.Next?.partial?.stream?.send(
      topic,
      "dragging",
      { card: cardId, active },
      { ack: false },
    );
  }

  async function moveCard(cardId, targetColumnId, targetPosition) {
    const partial = window.Next?.partial;
    if (!ctx.move_card_url || !partial?.submit) return;
    const move = { column: targetColumnId, position: targetPosition };
    movesInFlight.current.set(Number(cardId), move);
    setColumns((cols) =>
      applyMoveLocally(cols, cardId, targetColumnId, targetPosition),
    );
    setErrorMsg(null);
    const { status } = await partial.submit({
      action: ctx.move_card_url,
      data: {
        card_id: cardId,
        target_column: targetColumnId,
        target_position: targetPosition,
      },
      zone: "board",
      transport: "any",
      key: `card:${cardId}`,
    });
    if (movesInFlight.current.get(Number(cardId)) === move) {
      movesInFlight.current.delete(Number(cardId));
    }
    const message = MOVE_FAILED[status];
    if (!message) return;
    setColumns((cols) =>
      withLocalChanges(serverColumns.current, cols, movesInFlight.current),
    );
    setErrorMsg(message);
  }

  async function createCard(columnId, title) {
    const text = title.trim();
    const partial = window.Next?.partial;
    if (!ctx.create_card_url || !partial?.submit || !text) return;
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
    const { status, location } = await partial.submit({
      action: ctx.create_card_url,
      data: { column: columnId, title: text },
      navigate: false,
    });
    const cardId = status === "ok" ? readCreatedId(location) : null;
    if (cardId !== null) {
      setColumns((cols) => namePendingCard(cols, pendingId, cardId));
      return;
    }
    setColumns((cols) => dropCardLocally(cols, pendingId));
    if (status === "dropped") return;
    setErrorMsg(CREATE_FAILED[status] ?? CREATE_FAILED.invalid);
  }

  return (
    <div className="space-y-3">
      <Presence count={presence.count} members={presence.members} />
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
          <Column
            key={col.id}
            column={col}
            dragging={dragging}
            onDrop={moveCard}
            onCreate={createCard}
            onDragChange={announceDrag}
          />
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
