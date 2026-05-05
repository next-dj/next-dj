import { useState } from "react";
import { Card } from "../card/component";

export function Column({ column, onDrop }) {
  const [dropActive, setDropActive] = useState(false);

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
      <h3 className="text-sm font-semibold text-slate-700 px-1">{column.title}</h3>
      <div className="flex flex-col gap-2">
        {column.cards.map((card) => (
          <Card key={card.id} {...card} />
        ))}
      </div>
    </div>
  );
}
