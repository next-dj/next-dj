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
    expect(screen.getByText("Fix login bug")).toHaveAttribute("draggable", "true");
  });

  it("sets data-kanban-card attribute", () => {
    render(<Card id={42} title="Fix login bug" />);
    expect(screen.getByText("Fix login bug")).toHaveAttribute("data-kanban-card", "42");
  });

  it("puts card id into dataTransfer on dragstart", () => {
    render(<Card id={42} title="Fix login bug" />);
    const el = screen.getByText("Fix login bug");
    const setData = vi.fn();
    fireEvent.dragStart(el, { dataTransfer: { setData } });
    expect(setData).toHaveBeenCalledWith("text/card-id", "42");
  });

  it("adds opacity class while dragging", () => {
    render(<Card id={1} title="Fix login bug" />);
    const el = screen.getByText("Fix login bug");
    fireEvent.dragStart(el, { dataTransfer: { setData: vi.fn() } });
    expect(el.className).toContain("opacity-50");
  });

  it("removes opacity class after dragend", () => {
    render(<Card id={1} title="Fix login bug" />);
    const el = screen.getByText("Fix login bug");
    fireEvent.dragStart(el, { dataTransfer: { setData: vi.fn() } });
    fireEvent.dragEnd(el);
    expect(el.className).not.toContain("opacity-50");
  });
});
