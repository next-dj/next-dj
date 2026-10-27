import { useState } from "react";

export function Card({
  id,
  title,
  excerpt,
  pending = false,
  draggedBy = null,
  onDragChange,
}) {
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
        onDragChange?.(id, true);
      }}
      onDragEnd={() => {
        setDragging(false);
        onDragChange?.(id, false);
      }}
      className={`kanban-card rounded border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm${pending ? " opacity-60 cursor-progress" : " cursor-grab"}${dragging ? " opacity-50" : ""}${draggedBy ? " ring-2 ring-amber-400" : ""}`}
    >
      <div className="font-medium text-slate-900">{title}</div>
      {excerpt && (
        <div className="mt-1 text-xs text-slate-500" data-kanban-card-excerpt>
          {excerpt}
        </div>
      )}
      {draggedBy && (
        <div
          data-kanban-dragged-by={draggedBy.name}
          className="mt-2 flex items-center gap-1 text-xs text-amber-700"
        >
          <span
            aria-hidden="true"
            className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-amber-100 font-medium"
          >
            {draggedBy.initials}
          </span>
          <span>{draggedBy.name} is moving this card</span>
        </div>
      )}
    </div>
  );
}
