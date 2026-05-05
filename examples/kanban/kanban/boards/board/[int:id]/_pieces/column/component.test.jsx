import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Column } from "./component";

const makeColumn = (cards = []) => ({ id: 1, title: "To Do", cards });

describe("Column", () => {
  it("renders column title", () => {
    render(<Column column={makeColumn()} onDrop={vi.fn()} />);
    expect(screen.getByText("To Do")).toBeInTheDocument();
  });

  it("sets data-kanban-column attribute", () => {
    render(<Column column={makeColumn()} onDrop={vi.fn()} />);
    expect(document.querySelector("[data-kanban-column='1']")).toBeInTheDocument();
  });

  it("renders all cards", () => {
    const col = makeColumn([
      { id: 1, title: "Task A", position: 0 },
      { id: 2, title: "Task B", position: 1 },
    ]);
    render(<Column column={col} onDrop={vi.fn()} />);
    expect(screen.getByText("Task A")).toBeInTheDocument();
    expect(screen.getByText("Task B")).toBeInTheDocument();
  });

  it("calls onDrop with cardId, columnId and card count on drop", () => {
    const onDrop = vi.fn();
    const col = makeColumn([{ id: 10, title: "Task A", position: 0 }]);
    render(<Column column={col} onDrop={onDrop} />);
    const colEl = document.querySelector("[data-kanban-column='1']");
    fireEvent.drop(colEl, { dataTransfer: { getData: () => "7" } });
    expect(onDrop).toHaveBeenCalledWith("7", 1, 1);
  });

  it("adds drop-active class on dragover", () => {
    render(<Column column={makeColumn()} onDrop={vi.fn()} />);
    const colEl = document.querySelector("[data-kanban-column='1']");
    fireEvent.dragOver(colEl);
    expect(colEl.className).toContain("drop-active");
  });

  it("removes drop-active class on dragleave", () => {
    render(<Column column={makeColumn()} onDrop={vi.fn()} />);
    const colEl = document.querySelector("[data-kanban-column='1']");
    fireEvent.dragOver(colEl);
    fireEvent.dragLeave(colEl);
    expect(colEl.className).not.toContain("drop-active");
  });
});
