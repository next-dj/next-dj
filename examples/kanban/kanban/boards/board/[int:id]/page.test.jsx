import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Board } from "./page";

const mockBoard = {
  columns: [
    {
      id: 1,
      title: "Backlog",
      cards: [{ id: 10, title: "Task A", position: 0 }],
    },
    { id: 2, title: "Done", cards: [] },
  ],
  csrf: "test-csrf-token",
  move_card_url: "/actions/kanban/move_card",
};

beforeEach(() => {
  globalThis.window.Next = { context: { board: mockBoard } };
});

afterEach(() => {
  delete globalThis.window.Next;
  vi.restoreAllMocks();
});

describe("Board", () => {
  it("renders all columns from window.Next.context.board", () => {
    render(<Board />);
    expect(screen.getByText("Backlog")).toBeInTheDocument();
    expect(screen.getByText("Done")).toBeInTheDocument();
  });

  it("renders cards within their column", () => {
    render(<Board />);
    expect(screen.getByText("Task A")).toBeInTheDocument();
  });

  it("posts to move_card_url with correct payload on drop", async () => {
    const mockFetch = vi.fn().mockResolvedValue({ ok: true });
    globalThis.fetch = mockFetch;
    // jsdom does not allow spyOn on location.reload directly.
    Object.defineProperty(window, "location", {
      value: { ...window.location, reload: vi.fn() },
      writable: true,
    });

    render(<Board />);

    const col2 = document.querySelector("[data-kanban-column='2']");
    fireEvent.drop(col2, { dataTransfer: { getData: () => "10" } });

    await vi.waitFor(() => expect(mockFetch).toHaveBeenCalled());

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("/actions/kanban/move_card");
    expect(opts.method).toBe("POST");

    const body = new URLSearchParams(opts.body);
    expect(body.get("card_id")).toBe("10");
    expect(body.get("target_column_id")).toBe("2");
    expect(body.get("csrfmiddlewaretoken")).toBe("test-csrf-token");
  });

  it("renders empty board gracefully when context is missing", () => {
    delete globalThis.window.Next;
    render(<Board />);
    expect(document.querySelector("[data-kanban-column]")).toBeNull();
  });
});
