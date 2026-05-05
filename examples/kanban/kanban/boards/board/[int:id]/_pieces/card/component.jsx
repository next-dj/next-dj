import { useState } from "react";

export function Card({ id, title }) {
  const [dragging, setDragging] = useState(false);

  return (
    <div
      data-kanban-card={id}
      draggable
      onDragStart={(e) => {
        setDragging(true);
        e.dataTransfer.setData("text/card-id", String(id));
      }}
      onDragEnd={() => setDragging(false)}
      className={`kanban-card rounded border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm cursor-grab${dragging ? " opacity-50" : ""}`}
    >
      {title}
    </div>
  );
}
