import { useState } from "react";

export function Card({ id, title, excerpt, pending = false }) {
  const [dragging, setDragging] = useState(false);

  return (
    <div
      data-kanban-card={id}
      data-kanban-card-pending={pending ? "" : undefined}
      // A pending card holds a client-side id the server would not recognise,
      // so dragging stays off until the create response names the row.
      draggable={!pending}
      onDragStart={(e) => {
        setDragging(true);
        e.dataTransfer.setData("text/card-id", String(id));
      }}
      onDragEnd={() => setDragging(false)}
      className={`kanban-card rounded border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm${pending ? " opacity-60 cursor-progress" : " cursor-grab"}${dragging ? " opacity-50" : ""}`}
    >
      <div className="font-medium text-slate-900">{title}</div>
      {excerpt && (
        <div className="mt-1 text-xs text-slate-500" data-kanban-card-excerpt>
          {excerpt}
        </div>
      )}
    </div>
  );
}
