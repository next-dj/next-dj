import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { Board } from "./page";

const mockBoard = {
  columns: [
    {
      id: 1,
      title: "Backlog",
      wip_limit: null,
      cards: [{ id: 10, title: "Task A", position: 0 }],
    },
    { id: 2, title: "Done", wip_limit: null, cards: [] },
  ],
  csrf: "test-csrf-token",
  move_card_url: "/actions/kanban/move_card",
};

beforeEach(() => {
  globalThis.window.Next = {
    context: { board: structuredClone(mockBoard) },
  };
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

  it("applies the move optimistically before the fetch resolves", () => {
    let resolveFetch;
    globalThis.fetch = vi.fn(
      () => new Promise((resolve) => (resolveFetch = resolve)),
    );

    render(<Board />);

    expect(
      document
        .querySelector("[data-kanban-column='1']")
        .querySelector("[data-kanban-card]"),
    ).toBeTruthy();

    const col2 = document.querySelector("[data-kanban-column='2']");
    fireEvent.drop(col2, { dataTransfer: { getData: () => "10" } });

    expect(
      document
        .querySelector("[data-kanban-column='1']")
        .querySelector("[data-kanban-card]"),
    ).toBeNull();
    expect(
      document
        .querySelector("[data-kanban-column='2']")
        .querySelector("[data-kanban-card='10']"),
    ).toBeTruthy();

    resolveFetch({ ok: true });
  });

  it("rolls back to previous state and shows error when server rejects", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false });

    render(<Board />);

    const col2 = document.querySelector("[data-kanban-column='2']");
    fireEvent.drop(col2, { dataTransfer: { getData: () => "10" } });

    await vi.waitFor(() =>
      expect(document.querySelector("[data-kanban-error]")).toBeTruthy(),
    );

    expect(
      document
        .querySelector("[data-kanban-column='1']")
        .querySelector("[data-kanban-card='10']"),
    ).toBeTruthy();
    expect(screen.getByText(/rejected/i)).toBeInTheDocument();
  });

  it("rolls back on a network failure", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline"));

    render(<Board />);

    const col2 = document.querySelector("[data-kanban-column='2']");
    fireEvent.drop(col2, { dataTransfer: { getData: () => "10" } });

    await vi.waitFor(() =>
      expect(document.querySelector("[data-kanban-error]")).toBeTruthy(),
    );

    expect(
      document
        .querySelector("[data-kanban-column='1']")
        .querySelector("[data-kanban-card='10']"),
    ).toBeTruthy();
  });

  it("dismisses the error banner when the user clicks ×", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false });

    render(<Board />);
    const col2 = document.querySelector("[data-kanban-column='2']");
    fireEvent.drop(col2, { dataTransfer: { getData: () => "10" } });

    await vi.waitFor(() =>
      expect(document.querySelector("[data-kanban-error]")).toBeTruthy(),
    );

    fireEvent.click(screen.getByLabelText("Dismiss"));
    expect(document.querySelector("[data-kanban-error]")).toBeNull();
  });

  it("renders empty board gracefully when context is missing", () => {
    delete globalThis.window.Next;
    render(<Board />);
    expect(document.querySelector("[data-kanban-column]")).toBeNull();
  });
});
