import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Card } from "./component";

describe("Card", () => {
  it("renders the card title", () => {
    render(<Card id={1} title="Fix login bug" />);
    expect(screen.getByText("Fix login bug")).toBeInTheDocument();
  });

  it("is draggable", () => {
    render(<Card id={1} title="Fix login bug" />);
    expect(document.querySelector("[data-kanban-card='1']")).toHaveAttribute(
      "draggable",
      "true",
    );
  });

  it("sets data-kanban-card attribute", () => {
    render(<Card id={42} title="Fix login bug" />);
    expect(document.querySelector("[data-kanban-card='42']")).toBeTruthy();
  });

  it("puts card id into dataTransfer on dragstart", () => {
    render(<Card id={42} title="Fix login bug" />);
    const el = document.querySelector("[data-kanban-card='42']");
    const setData = vi.fn();
    fireEvent.dragStart(el, { dataTransfer: { setData } });
    expect(setData).toHaveBeenCalledWith("text/card-id", "42");
  });

  it("adds opacity class while dragging", () => {
    render(<Card id={1} title="Fix login bug" />);
    const el = document.querySelector("[data-kanban-card='1']");
    fireEvent.dragStart(el, { dataTransfer: { setData: vi.fn() } });
    expect(el.className).toContain("opacity-50");
  });

  it("removes opacity class after dragend", () => {
    render(<Card id={1} title="Fix login bug" />);
    const el = document.querySelector("[data-kanban-card='1']");
    fireEvent.dragStart(el, { dataTransfer: { setData: vi.fn() } });
    fireEvent.dragEnd(el);
    expect(el.className).not.toContain("opacity-50");
  });

  it("renders the excerpt when provided", () => {
    render(<Card id={1} title="Fix login bug" excerpt="Repro on staging" />);
    expect(screen.getByText("Repro on staging")).toBeInTheDocument();
    expect(screen.getByText("Repro on staging")).toHaveAttribute(
      "data-kanban-card-excerpt",
    );
  });

  it("does not render an excerpt node when excerpt is empty", () => {
    render(<Card id={1} title="Fix login bug" />);
    expect(document.querySelector("[data-kanban-card-excerpt]")).toBeNull();
  });
});
