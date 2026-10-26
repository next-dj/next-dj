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
  topic: "board:1",
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

let runtime;

function fakeRuntime() {
  const bus = new Map();
  const topics = new Map();
  const subscribe = (registry, key, listener) => {
    registry.set(key, [...(registry.get(key) ?? []), listener]);
    return () =>
      registry.set(
        key,
        registry.get(key).filter((l) => l !== listener),
      );
  };
  const answers = [];
  const submit = vi.fn(() => new Promise((resolve) => answers.push(resolve)));
  const send = vi.fn(async () => true);
  window.Next = {
    context: { board: structuredClone(mockBoard) },
    on: (event, listener) => subscribe(bus, event, listener),
    partial: {
      submit,
      stream: {
        on: (topic, name, listener) => subscribe(topics, `${topic} ${name}`, listener),
        meta: () => ({ topic: "board:1", connection: "own-socket" }),
        send,
      },
    },
  };
  const emit = (event, payload) =>
    act(() => {
      for (const listener of bus.get(event) ?? []) listener(payload);
    });
  return {
    submit,
    send,
    emit,
    answer: (status, extra = {}) =>
      act(async () => answers.shift()({ status, applied: true, ...extra })),
    push: (topic, name, detail) =>
      act(() => {
        for (const listener of topics.get(`${topic} ${name}`) ?? []) listener(detail);
      }),
    listeners: () => [...bus.values(), ...topics.values()].flat().length,
  };
}

const dropOn = (columnId, cardId = "10") =>
  fireEvent.drop(document.querySelector(`[data-kanban-column='${columnId}']`), {
    dataTransfer: { getData: () => cardId },
  });

const cardIn = (columnId, cardId) =>
  document.querySelector(
    `[data-kanban-column='${columnId}'] [data-kanban-card='${cardId}']`,
  );

const movedColumns = () => {
  const columns = structuredClone(mockBoard.columns);
  columns[1].cards = columns[0].cards;
  columns[0].cards = [];
  return columns;
};

const serverBoard = (columns) => ({
  context: { board: { ...mockBoard, columns } },
  changed: ["board"],
});

const grace = { name: "Grace Hopper", initials: "GH" };

beforeEach(() => {
  runtime = fakeRuntime();
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

  it("submits the move action over any transport into the board zone", () => {
    render(<Board />);
    dropOn(2);
    expect(runtime.submit.mock.calls).toEqual([
      [
        {
          action: "/actions/kanban/move_card",
          data: { card_id: "10", target_column: 2, target_position: 0 },
          zone: "board",
          transport: "any",
          key: "card:10",
        },
      ],
    ]);
  });

  it("applies the move before the server answers", () => {
    render(<Board />);
    dropOn(2);
    expect(cardIn(1, 10)).toBeNull();
    expect(cardIn(2, 10)).toBeTruthy();
  });

  it("hands a second drop to the runtime while the first is in flight", () => {
    render(<Board />);
    dropOn(2);
    dropOn(1);
    expect(runtime.submit).toHaveBeenCalledTimes(2);
    expect(cardIn(1, 10)).toBeTruthy();
  });

  it("keeps the move once the server accepts it", async () => {
    render(<Board />);
    dropOn(2);
    await runtime.answer("ok");
    expect(cardIn(2, 10)).toBeTruthy();
    expect(document.querySelector("[data-kanban-error]")).toBeNull();
  });

  it("leaves a move a newer drop replaced to the reply of that drop", async () => {
    render(<Board />);
    dropOn(2);
    dropOn(1);
    await runtime.answer("dropped");
    expect(cardIn(1, 10)).toBeTruthy();
    expect(document.querySelector("[data-kanban-error]")).toBeNull();
  });

  it("keeps a second move in flight through the board the first reply carries", async () => {
    window.Next.context.board.columns[0].cards.push({
      id: 11,
      title: "Task B",
      position: 1,
    });
    render(<Board />);
    dropOn(2, "10");
    dropOn(2, "11");
    const columns = structuredClone(window.Next.context.board.columns);
    columns[1].cards = [{ ...columns[0].cards[0], position: 0 }];
    columns[0].cards = [{ ...columns[0].cards[1], position: 0 }];
    runtime.emit("context-updated", serverBoard(columns));
    await runtime.answer("ok");
    expect(cardIn(2, 10)).toBeTruthy();
    expect(cardIn(2, 11)).toBeTruthy();
    expect(cardIn(1, 11)).toBeNull();
    await runtime.answer("invalid");
    expect(cardIn(2, 10)).toBeTruthy();
    expect(cardIn(1, 11)).toBeTruthy();
  });

  it("skips the move when the runtime offers no submit", () => {
    delete window.Next.partial.submit;
    render(<Board />);
    dropOn(2);
    expect(cardIn(1, 10)).toBeTruthy();
  });

  it("skips the move when the context carries no action url", () => {
    delete window.Next.context.board.move_card_url;
    render(<Board />);
    dropOn(2);
    expect(runtime.submit).not.toHaveBeenCalled();
    expect(cardIn(1, 10)).toBeTruthy();
  });

  it("rolls back onto the board the invalid reply carries and alerts", async () => {
    render(<Board />);
    dropOn(2);
    const columns = structuredClone(mockBoard.columns);
    columns[0].cards = [];
    runtime.emit("context-updated", serverBoard(columns));
    await runtime.answer("invalid");
    expect(document.querySelector("[data-kanban-card='10']")).toBeNull();
    expect(screen.getByText("Move rejected by server.")).toBeInTheDocument();
  });

  it("rolls back when the move never reaches the server", async () => {
    render(<Board />);
    dropOn(2);
    await runtime.answer("error");
    expect(cardIn(1, 10)).toBeTruthy();
    expect(screen.getByText(/rolled back/i)).toBeInTheDocument();
  });

  it("dismisses the error banner when the user clicks ×", async () => {
    render(<Board />);
    dropOn(2);
    await runtime.answer("error");
    fireEvent.click(screen.getByLabelText("Dismiss"));
    expect(document.querySelector("[data-kanban-error]")).toBeNull();
  });

  it("adopts the board another tab changed", () => {
    render(<Board />);
    runtime.emit("context-updated", {
      context: { board: { ...mockBoard, columns: movedColumns() } },
      changed: ["board"],
    });
    expect(cardIn(2, 10)).toBeTruthy();
    expect(cardIn(1, 10)).toBeNull();
  });

  it("keeps a pending card through a server update", () => {
    render(<Board />);
    submitNewCard(2, "Write docs");
    runtime.emit("context-updated", {
      context: { board: { ...mockBoard, columns: movedColumns() } },
      changed: ["board"],
    });
    expect(cardIn(2, 10)).toBeTruthy();
    expect(document.querySelector("[data-kanban-card-pending]").textContent).toContain(
      "Write docs",
    );
  });

  it("ignores a context update that leaves the board alone", () => {
    render(<Board />);
    runtime.emit("context-updated", {
      context: { board: { ...mockBoard, columns: movedColumns() } },
      changed: ["flash"],
    });
    runtime.emit("context-updated", { context: {}, changed: ["board"] });
    expect(cardIn(1, 10)).toBeTruthy();
  });

  it("drops a pending card the server update already delivered", async () => {
    render(<Board />);
    submitNewCard(2, "Write docs");
    const columns = structuredClone(mockBoard.columns);
    columns[1].cards = [{ id: 77, title: "Write docs", position: 0 }];
    runtime.emit("context-updated", {
      context: { board: { ...mockBoard, columns } },
      changed: ["board"],
    });
    await runtime.answer("ok", { location: "http://localhost/board/1/?created=77" });
    expect(document.querySelectorAll("[data-kanban-card='77']")).toHaveLength(1);
    expect(document.querySelector("[data-kanban-card-pending]")).toBeNull();
  });

  it("keeps a move that landed while a rejected create was in flight", async () => {
    render(<Board />);
    submitNewCard(2, "Write docs");
    dropOn(2);
    await runtime.answer("error");
    expect(cardIn(2, 10)).toBeTruthy();
    expect(screen.queryByText("Write docs")).toBeNull();
  });

  it("shows who watches the board from the presence event", () => {
    render(<Board />);
    expect(document.querySelector("[data-kanban-presence]")).toBeNull();
    runtime.push("board:1", "next:presence", {
      count: 3,
      members: [
        { name: "Ada Lovelace", initials: "AL" },
        { name: "Grace Hopper", initials: "GH" },
      ],
    });
    const region = screen.getByRole("region", { name: "Watching this board" });
    expect(region.querySelectorAll("[data-kanban-member]")).toHaveLength(2);
    expect(region).toHaveTextContent("Ada Lovelace");
    expect(region.querySelector("[data-kanban-guests]")).toHaveTextContent("+1 guest");
  });

  it("counts guests alone when nobody watching is signed in", () => {
    render(<Board />);
    runtime.push("board:1", "next:presence", { count: 2 });
    expect(document.querySelector("[data-kanban-guests]")).toHaveTextContent(
      "2 guests",
    );
    expect(document.querySelector("[data-kanban-member]")).toBeNull();
  });

  it("marks the card someone else is dragging until they drop it", () => {
    render(<Board />);
    const from = "other-socket";
    runtime.push("board:1", "board:dragging", {
      card: 10,
      active: true,
      by: grace,
      from,
    });
    expect(cardIn(1, 10)).toHaveTextContent("Grace Hopper is moving this card");
    runtime.push("board:1", "board:dragging", {
      card: 10,
      active: false,
      by: grace,
      from,
    });
    expect(cardIn(1, 10)).not.toHaveTextContent("is moving this card");
  });

  it("sends the drag start and end upstream", () => {
    render(<Board />);
    const card = cardIn(1, 10);
    fireEvent.dragStart(card, { dataTransfer: { setData: () => {} } });
    fireEvent.dragEnd(card);
    expect(runtime.send.mock.calls).toEqual([
      ["board:1", "dragging", { card: 10, active: true }, { ack: false }],
      ["board:1", "dragging", { card: 10, active: false }, { ack: false }],
    ]);
  });

  it("ends the drag on a drop into another column whose dragend never arrives", () => {
    render(<Board />);
    fireEvent.dragStart(cardIn(1, 10), { dataTransfer: { setData: () => {} } });
    dropOn(2);
    fireEvent.dragEnd(cardIn(2, 10));
    expect(runtime.send.mock.calls).toEqual([
      ["board:1", "dragging", { card: 10, active: true }, { ack: false }],
      ["board:1", "dragging", { card: 10, active: false }, { ack: false }],
    ]);
  });

  it("announces no drag end for a drop it never announced a start of", () => {
    render(<Board />);
    dropOn(2);
    expect(runtime.send).not.toHaveBeenCalled();
  });

  it("keeps the mark while another tab of the same member still holds the card", () => {
    render(<Board />);
    const ada = { name: "Ada Lovelace", initials: "AL", key: "ada" };
    for (const from of ["first-tab", "second-tab"]) {
      const detail = { card: 10, active: true, by: ada, from };
      runtime.push("board:1", "board:dragging", detail);
    }
    runtime.push("board:1", "board:dragging", {
      card: 10,
      active: false,
      by: ada,
      from: "first-tab",
    });
    expect(cardIn(1, 10)).toHaveTextContent("Ada Lovelace is moving this card");
    runtime.push("board:1", "board:dragging", {
      card: 10,
      active: false,
      by: ada,
      from: "second-tab",
    });
    expect(cardIn(1, 10)).not.toHaveTextContent("is moving this card");
  });

  it("clears the marks of a socket the server reports closed", () => {
    render(<Board />);
    const ada = { name: "Ada Lovelace", initials: "AL", key: "ada" };
    runtime.push("board:1", "board:dragging", {
      card: 10,
      active: true,
      by: ada,
      from: "closed-tab",
    });
    runtime.push("board:1", "next:presence", { count: 1, members: [ada] });
    runtime.push("board:1", "board:released", { from: "open-tab" });
    expect(cardIn(1, 10)).toHaveTextContent("Ada Lovelace is moving this card");
    runtime.push("board:1", "board:released", { from: "closed-tab" });
    expect(cardIn(1, 10)).not.toHaveTextContent("is moving this card");
  });

  it("skips the echo its own socket sent", () => {
    render(<Board />);
    runtime.push("board:1", "board:dragging", {
      card: 10,
      active: true,
      by: { name: "Ada Lovelace", initials: "AL" },
      from: "own-socket",
    });
    expect(cardIn(1, 10)).not.toHaveTextContent("is moving this card");
  });

  it("clears the mark once the member dragging the card leaves the board", () => {
    render(<Board />);
    runtime.push("board:1", "board:dragging", {
      card: 10,
      active: true,
      by: grace,
      from: "other-socket",
    });
    runtime.push("board:1", "next:presence", { count: 2, members: [grace] });
    expect(cardIn(1, 10)).toHaveTextContent("Grace Hopper is moving this card");
    runtime.push("board:1", "next:presence", { count: 1 });
    expect(cardIn(1, 10)).not.toHaveTextContent("is moving this card");
  });

  it("clears the mark when only a namesake of the member dragging stays", () => {
    render(<Board />);
    runtime.push("board:1", "board:dragging", {
      card: 10,
      active: true,
      by: { ...grace, key: "first" },
      from: "other-socket",
    });
    runtime.push("board:1", "next:presence", {
      count: 1,
      members: [{ ...grace, key: "second" }],
    });
    expect(cardIn(1, 10)).not.toHaveTextContent("is moving this card");
  });

  it("stays off the stream when the board names no topic", () => {
    delete window.Next.context.board.topic;
    render(<Board />);
    const card = cardIn(1, 10);
    fireEvent.dragStart(card, { dataTransfer: { setData: () => {} } });
    expect(runtime.send).not.toHaveBeenCalled();
    expect(runtime.listeners()).toBe(1);
  });

  it("releases every listener on unmount", () => {
    const { unmount } = render(<Board />);
    expect(runtime.listeners()).toBe(4);
    unmount();
    expect(runtime.listeners()).toBe(0);
  });

  it("submits the create action without navigating", () => {
    render(<Board />);
    submitNewCard(2, "Write docs");
    expect(runtime.submit.mock.calls).toEqual([
      [
        {
          action: "/actions/kanban/create_card",
          data: { column: 2, title: "Write docs" },
          navigate: false,
        },
      ],
    ]);
  });

  it("shows the card as pending before the server answers", () => {
    render(<Board />);
    submitNewCard(2, "Write docs");
    const pending = document.querySelector(
      "[data-kanban-column='2'] [data-kanban-card-pending]",
    );
    expect(pending).toBeTruthy();
    expect(pending.textContent).toContain("Write docs");
  });

  it("adopts the card id the location carries", async () => {
    render(<Board />);
    submitNewCard(2, "Write docs");
    await runtime.answer("ok", { location: "http://localhost/board/1/?created=77" });
    expect(
      document.querySelector("[data-kanban-column='2'] [data-kanban-card='77']"),
    ).toBeTruthy();
    expect(document.querySelector("[data-kanban-card-pending]")).toBeNull();
  });

  it("rolls back the new card when the server rejects it", async () => {
    render(<Board />);
    submitNewCard(2, "Over the limit");
    await runtime.answer("invalid");
    expect(document.querySelector("[data-kanban-error]")).toBeTruthy();
    expect(screen.queryByText("Over the limit")).toBeNull();
    expect(
      document.querySelector("[data-kanban-column='2'] [data-kanban-card]"),
    ).toBeNull();
  });

  it("shows the validation message an invalid create carries", async () => {
    render(<Board />);
    submitNewCard(2, "Over the limit");
    await runtime.answer("invalid", {
      errors: { __all__: ["Column has reached its WIP limit."] },
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Column has reached its WIP limit.",
    );
  });

  it("shows the reason a 400 refusal of the create names", async () => {
    render(<Board />);
    submitNewCard(2, "Over the limit");
    await runtime.answer("error", {
      error: { kind: "http", status: 400, body: "Column has reached its WIP limit.\n" },
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      /^Column has reached its WIP limit\.×$/,
    );
    expect(screen.queryByText("Over the limit")).toBeNull();
  });

  it("falls back to the network message for a blank 400 or a 500", async () => {
    render(<Board />);
    submitNewCard(2, "Blank refusal");
    await runtime.answer("error", { error: { kind: "http", status: 400, body: " " } });
    expect(screen.getByRole("alert")).toHaveTextContent(/Network error/);
    submitNewCard(2, "Server failure");
    await runtime.answer("error", {
      error: { kind: "http", status: 500, body: "Server Error" },
    });
    expect(screen.getByRole("alert")).toHaveTextContent(/Network error/);
  });

  it("ignores a drop of a card the board does not hold", () => {
    render(<Board />);
    dropOn(2, "999");
    expect(cardIn(1, 10)).toBeTruthy();
    expect(
      document.querySelector("[data-kanban-column='2'] [data-kanban-card]"),
    ).toBeNull();
  });

  it("shows a column the server added after the first render", () => {
    render(<Board />);
    const columns = structuredClone(mockBoard.columns);
    columns.push({ id: 3, title: "Review", wip_limit: null, cards: [] });
    runtime.emit("context-updated", serverBoard(columns));
    expect(screen.getByText("Review")).toBeInTheDocument();
  });

  it("renumbers the siblings of a card moved out of a column", () => {
    render(<Board />);
    submitNewCard(1, "Write docs");
    dropOn(2);
    expect(cardIn(2, 10)).toBeTruthy();
    expect(document.querySelector("[data-kanban-card-pending]")).toBeTruthy();
  });

  it("rolls back a success the location never named", async () => {
    render(<Board />);
    submitNewCard(2, "Write docs");
    await runtime.answer("ok", { location: "http://localhost/board/1/" });
    expect(document.querySelector("[data-kanban-error]")).toBeTruthy();
    expect(document.querySelector("[data-kanban-card-pending]")).toBeNull();
    expect(screen.queryByText("Write docs")).toBeNull();
  });

  it("rolls back a success that carried no location", async () => {
    render(<Board />);
    submitNewCard(2, "Write docs");
    await runtime.answer("ok");
    expect(screen.queryByText("Write docs")).toBeNull();
  });

  it("withdraws a create the runtime dropped without an alert", async () => {
    render(<Board />);
    submitNewCard(2, "Write docs");
    await runtime.answer("dropped");
    expect(screen.queryByText("Write docs")).toBeNull();
    expect(document.querySelector("[data-kanban-error]")).toBeNull();
  });

  it("rolls back the new card on a network failure", async () => {
    render(<Board />);
    submitNewCard(2, "Write docs");
    await runtime.answer("error");
    expect(screen.getByText(/rolled back/i)).toBeInTheDocument();
    expect(screen.queryByText("Write docs")).toBeNull();
  });

  it("skips the create request when the context carries no action url", () => {
    delete globalThis.window.Next.context.board.create_card_url;
    render(<Board />);
    submitNewCard(2, "Write docs");
    expect(runtime.submit).not.toHaveBeenCalled();
    expect(screen.queryByText("Write docs")).toBeNull();
  });

  it("skips the create request when the runtime offers no submit", () => {
    delete window.Next.partial.submit;
    render(<Board />);
    submitNewCard(2, "Write docs");
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

  it("keeps the live root when onMount re-runs on the same element", () => {
    act(() => island.mount(el));

    dropOn(2);
    expect(
      el.querySelector("[data-kanban-column='2'] [data-kanban-card='10']"),
    ).toBeTruthy();

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
