import ReactDOM from "react-dom/client";
import { useState } from "react";
import { Column } from "./_pieces/column/component";

export function Board() {
  const ctx = window.Next?.context?.board ?? {};
  const [columns, setColumns] = useState(ctx.columns ?? []);

  async function moveCard(cardId, targetColumnId, targetPosition) {
    if (!ctx.move_card_url) return;
    await fetch(ctx.move_card_url, {
      method: "POST",
      headers: { "X-CSRFToken": ctx.csrf ?? "" },
      body: new URLSearchParams({
        card_id: cardId,
        target_column_id: targetColumnId,
        target_position: String(targetPosition),
        csrfmiddlewaretoken: ctx.csrf ?? "",
      }),
    });
    window.location.reload();
  }

  return (
    <div className="flex gap-4 overflow-x-auto pb-4">
      {columns.map((col) => (
        <Column key={col.id} column={col} onDrop={moveCard} />
      ))}
    </div>
  );
}

const el = document.getElementById("kanban-board");
if (el) ReactDOM.createRoot(el).render(<Board />);
