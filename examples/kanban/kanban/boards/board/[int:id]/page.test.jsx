import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { Board } from "./page";

const island = vi.hoisted(() => {
  const captured = { selector: null, mount: null };
  window.Next = {
    context: {},
    partial: {
      onMount: (selector, callback) => {
        captured.selector = selector;
        captured.mount = callback;
      },
    },
  };
  return captured;
});

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
  create_card_url: "/actions/kanban/create_card",
};

const submitNewCard = (columnId, title) => {
  const column = document.querySelector(`[data-kanban-column='${columnId}']`);
  fireEvent.change(column.querySelector("input[name='title']"), {
    target: { value: title },
  });
  fireEvent.submit(column.querySelector("form"));
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

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());

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
    globalThis.fetch = vi.fn(() => new Promise((resolve) => (resolveFetch = resolve)));

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

    await waitFor(() =>
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

    await waitFor(() =>
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

    await waitFor(() =>
      expect(document.querySelector("[data-kanban-error]")).toBeTruthy(),
    );

    fireEvent.click(screen.getByLabelText("Dismiss"));
    expect(document.querySelector("[data-kanban-error]")).toBeNull();
  });

  it("posts to create_card_url with the column id and title", async () => {
    const mockFetch = vi
      .fn()
      .mockResolvedValue({ ok: true, url: "/board/1/?created=77" });
    globalThis.fetch = mockFetch;

    render(<Board />);
    submitNewCard(2, "Write docs");

    await waitFor(() => expect(mockFetch).toHaveBeenCalled());

    const [url, opts] = mockFetch.mock.calls[0];
    expect(url).toBe("/actions/kanban/create_card");
    expect(opts.method).toBe("POST");

    const body = new URLSearchParams(opts.body);
    expect(body.get("column_id")).toBe("2");
    expect(body.get("title")).toBe("Write docs");
    expect(body.get("csrfmiddlewaretoken")).toBe("test-csrf-token");
  });

  it("shows the card as pending before the fetch resolves", () => {
    let resolveFetch;
    globalThis.fetch = vi.fn(() => new Promise((resolve) => (resolveFetch = resolve)));

    render(<Board />);
    submitNewCard(2, "Write docs");

    const pending = document.querySelector(
      "[data-kanban-column='2'] [data-kanban-card-pending]",
    );
    expect(pending).toBeTruthy();
    expect(pending.textContent).toContain("Write docs");

    resolveFetch({ ok: true, url: "/board/1/?created=77" });
  });

  it("adopts the card id the redirect carries", async () => {
    globalThis.fetch = vi
      .fn()
      .mockResolvedValue({ ok: true, url: "/board/1/?created=77" });

    render(<Board />);
    submitNewCard(2, "Write docs");

    await waitFor(() =>
      expect(
        document.querySelector("[data-kanban-column='2'] [data-kanban-card='77']"),
      ).toBeTruthy(),
    );
    expect(document.querySelector("[data-kanban-card-pending]")).toBeNull();
  });

  it("rolls back the new card when the server rejects it", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, url: "/board/1/" });

    render(<Board />);
    submitNewCard(2, "Over the limit");

    await waitFor(() =>
      expect(document.querySelector("[data-kanban-error]")).toBeTruthy(),
    );
    expect(screen.queryByText("Over the limit")).toBeNull();
    expect(
      document.querySelector("[data-kanban-column='2'] [data-kanban-card]"),
    ).toBeNull();
  });

  it("rolls back a success the redirect never named", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, url: "/board/1/" });

    render(<Board />);
    submitNewCard(2, "Write docs");

    await waitFor(() =>
      expect(document.querySelector("[data-kanban-error]")).toBeTruthy(),
    );
    expect(document.querySelector("[data-kanban-card-pending]")).toBeNull();
    expect(screen.queryByText("Write docs")).toBeNull();
  });

  it("keeps a move that landed while a rejected create was in flight", async () => {
    let rejectCreate;
    globalThis.fetch = vi.fn((url) =>
      url === "/actions/kanban/create_card"
        ? new Promise((resolve) => {
            rejectCreate = () => resolve({ ok: false, url: "/board/1/" });
          })
        : Promise.resolve({ ok: true }),
    );

    render(<Board />);
    submitNewCard(2, "Write docs");
    fireEvent.drop(document.querySelector("[data-kanban-column='2']"), {
      dataTransfer: { getData: () => "10" },
    });

    await act(async () => rejectCreate());

    expect(
      document.querySelector("[data-kanban-column='2'] [data-kanban-card='10']"),
    ).toBeTruthy();
    expect(screen.queryByText("Write docs")).toBeNull();
  });

  it("rolls back the new card on a network failure", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("offline"));

    render(<Board />);
    submitNewCard(2, "Write docs");

    await waitFor(() =>
      expect(document.querySelector("[data-kanban-error]")).toBeTruthy(),
    );
    expect(screen.getByText(/rolled back/i)).toBeInTheDocument();
    expect(screen.queryByText("Write docs")).toBeNull();
  });

  it("skips the create request when the context carries no action url", () => {
    delete globalThis.window.Next.context.board.create_card_url;
    const mockFetch = vi.fn();
    globalThis.fetch = mockFetch;

    render(<Board />);
    submitNewCard(2, "Write docs");

    expect(mockFetch).not.toHaveBeenCalled();
    expect(screen.queryByText("Write docs")).toBeNull();
  });

  it("renders empty board gracefully when context is missing", () => {
    delete globalThis.window.Next;
    render(<Board />);
    expect(document.querySelector("[data-kanban-column]")).toBeNull();
  });
});

describe("board island lifecycle", () => {
  let el;

  beforeEach(() => {
    el = document.createElement("div");
    el.id = "kanban-board";
    document.body.append(el);
  });

  afterEach(() => {
    act(() => {
      el.dispatchEvent(new CustomEvent("next:removed", { bubbles: true }));
    });
    el.remove();
  });

  it("registers the mount callback for the board mount point", () => {
    expect(island.selector).toBe("#kanban-board");
    expect(island.mount).toBeTypeOf("function");
  });

  it("mounts the board into the element onMount hands over", () => {
    act(() => island.mount(el));
    expect(el.querySelectorAll("[data-kanban-column]")).toHaveLength(2);
  });

  it("keeps the live root when onMount re-runs on the same element", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true });
    act(() => island.mount(el));

    const col2 = el.querySelector("[data-kanban-column='2']");
    fireEvent.drop(col2, { dataTransfer: { getData: () => "10" } });
    await waitFor(() =>
      expect(
        el.querySelector("[data-kanban-column='2'] [data-kanban-card='10']"),
      ).toBeTruthy(),
    );

    act(() => island.mount(el));

    expect(
      el.querySelector("[data-kanban-column='2'] [data-kanban-card='10']"),
    ).toBeTruthy();
    expect(el.querySelector("[data-kanban-column='1'] [data-kanban-card]")).toBeNull();
  });

  it("unmounts the root when next:removed fires on the mount point", () => {
    act(() => island.mount(el));
    expect(el.querySelector("[data-kanban-column]")).toBeTruthy();

    act(() => {
      el.dispatchEvent(new CustomEvent("next:removed", { bubbles: true }));
    });

    expect(el.querySelector("[data-kanban-column]")).toBeNull();
  });

  it("unmounts a board nested inside a removed subtree", () => {
    const wrapper = document.createElement("section");
    document.body.append(wrapper);
    wrapper.append(el);
    act(() => island.mount(el));

    act(() => {
      wrapper.dispatchEvent(new CustomEvent("next:removed", { bubbles: true }));
    });

    expect(el.querySelector("[data-kanban-column]")).toBeNull();
    wrapper.remove();
  });

  it("mounts a fresh root after the previous one was removed", () => {
    act(() => island.mount(el));
    act(() => {
      el.dispatchEvent(new CustomEvent("next:removed", { bubbles: true }));
    });
    expect(el.querySelector("[data-kanban-column]")).toBeNull();

    act(() => island.mount(el));

    expect(el.querySelectorAll("[data-kanban-column]")).toHaveLength(2);
  });

  it("ignores next:removed events without an element target", () => {
    act(() => island.mount(el));

    document.dispatchEvent(new CustomEvent("next:removed", { bubbles: true }));

    expect(el.querySelectorAll("[data-kanban-column]")).toHaveLength(2);
  });

  it("keeps the board when an unrelated element is removed", () => {
    act(() => island.mount(el));
    const other = document.createElement("div");
    document.body.append(other);

    other.dispatchEvent(new CustomEvent("next:removed", { bubbles: true }));

    expect(el.querySelectorAll("[data-kanban-column]")).toHaveLength(2);
    other.remove();
  });
});
