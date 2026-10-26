import { beforeEach, describe, expect, it, vi } from "vitest";

let mountChart;
let contextUpdated;
let voteCountUpdated;
let phaseUpdated;
let presenceUpdated;
let serverNow;
const unsubscribe = vi.fn();

beforeEach(async () => {
  vi.resetModules();
  mountChart = null;
  contextUpdated = null;
  voteCountUpdated = null;
  phaseUpdated = null;
  presenceUpdated = null;
  serverNow = 1000;
  window.Next = {
    partial: {
      stream: {
        now: () => serverNow,
        on: (topic, name, callback) => {
          expect(topic).toBe("poll:1");
          if (name === "poll:phase") phaseUpdated = callback;
          else if (name === "next:presence") presenceUpdated = callback;
          else throw new Error(`unexpected topic event: ${name}`);
          return unsubscribe;
        },
      },
      onMount: (selector, callback) => {
        expect(selector).toBe("[data-poll-chart]");
        mountChart = callback;
      },
    },
    on: (event, callback) => {
      if (event === "context-updated") contextUpdated = callback;
      else if (event === "poll:votes") voteCountUpdated = callback;
      else throw new Error(`unexpected event: ${event}`);
    },
  };
  await import("./page.vue");
});

function chartRoot({ total = 5, choices } = {}) {
  const items = (
    choices ?? [
      { id: 10, text: "Tabs", votes: 3 },
      { id: 11, text: "Spaces", votes: 2 },
    ]
  )
    .map(
      (c) =>
        `<span data-choice-id="${c.id}" data-choice-text="${c.text}" data-choice-votes="${c.votes}"></span>`,
    )
    .join("");
  const root = document.createElement("div");
  root.dataset.pollChart = "1";
  root.dataset.pollOpen = "true";
  root.dataset.pollDeadline = "";
  root.innerHTML = `
    <div data-poll-chart-data data-total-votes="${total}" hidden>${items}</div>
    <div data-poll-chart-app id="poll-chart-app" data-next-keep></div>
    <p data-poll-status></p>
    <p data-poll-countdown aria-hidden="true"></p>
    <button type="submit">Vote</button>
  `;
  return root;
}

describe("poll chart mount", () => {
  it("mounts the Vue app reading the data block snapshot", async () => {
    const root = chartRoot();
    mountChart(root);
    await Promise.resolve();
    const app = root.querySelector("[data-poll-chart-app]");
    expect(app.querySelectorAll(".poll-chart-row")).toHaveLength(2);
    expect(app.querySelector("[data-poll-chart-total]").textContent).toBe("5");
    expect(
      app.querySelector('[data-choice-id="10"] [data-poll-chart-votes]').textContent,
    ).toBe("3");
  });

  it("pushes a fresh snapshot into the live instance on re-mount", async () => {
    const root = chartRoot();
    mountChart(root);
    await Promise.resolve();
    const data = root.querySelector("[data-poll-chart-data]");
    data.dataset.totalVotes = "10";
    data.querySelector('[data-choice-id="10"]').dataset.choiceVotes = "8";
    mountChart(root);
    await Promise.resolve();
    const app = root.querySelector("[data-poll-chart-app]");
    expect(app.querySelector("[data-poll-chart-total]").textContent).toBe("10");
    expect(
      app.querySelector('[data-choice-id="10"] [data-poll-chart-votes]').textContent,
    ).toBe("8");
  });

  it("does nothing when the data block is absent", () => {
    const root = document.createElement("div");
    root.dataset.pollChart = "1";
    root.innerHTML = '<div data-poll-chart-app id="poll-chart-app"></div>';
    mountChart(root);
    expect(
      root.querySelector("[data-poll-chart-app]").querySelectorAll(".poll-chart-row"),
    ).toHaveLength(0);
  });

  it("does nothing when the app container is absent", () => {
    const root = chartRoot();
    root.querySelector("[data-poll-chart-app]").remove();
    expect(() => mountChart(root)).not.toThrow();
  });

  it("applies the pushed snapshot when live_results is in changed", async () => {
    const root = chartRoot();
    document.body.append(root);
    mountChart(root);
    await Promise.resolve();
    contextUpdated({
      context: {
        live_results: {
          poll_id: 1,
          total_votes: 12,
          choices: [
            { id: 10, text: "Tabs", votes: 9 },
            { id: 11, text: "Spaces", votes: 3 },
          ],
        },
      },
      changed: ["live_results"],
    });
    await Promise.resolve();
    const app = root.querySelector("[data-poll-chart-app]");
    expect(app.querySelector("[data-poll-chart-total]").textContent).toBe("12");
    expect(
      app.querySelector('[data-choice-id="10"] [data-poll-chart-votes]').textContent,
    ).toBe("9");
    root.remove();
  });

  it("skips context-updated when live_results is not in changed", async () => {
    const root = chartRoot();
    document.body.append(root);
    mountChart(root);
    await Promise.resolve();
    contextUpdated({
      context: {
        live_results: {
          poll_id: 1,
          total_votes: 99,
          choices: [
            { id: 10, text: "Tabs", votes: 99 },
            { id: 11, text: "Spaces", votes: 0 },
          ],
        },
      },
      changed: ["flash_messages"],
    });
    await Promise.resolve();
    const app = root.querySelector("[data-poll-chart-app]");
    expect(app.querySelector("[data-poll-chart-total]").textContent).toBe("5");
    expect(
      app.querySelector('[data-choice-id="10"] [data-poll-chart-votes]').textContent,
    ).toBe("3");
    root.remove();
  });

  it("ignores context-updated when no snapshot is present", () => {
    const root = chartRoot();
    document.body.append(root);
    mountChart(root);
    expect(() =>
      contextUpdated({ context: {}, changed: ["live_results"] }),
    ).not.toThrow();
    root.remove();
  });
});

describe("poll chart unmount", () => {
  it("unmounts the app when next:removed fires on the island root", async () => {
    const root = chartRoot();
    document.body.append(root);
    mountChart(root);
    await Promise.resolve();
    const app = root.querySelector("[data-poll-chart-app]");
    expect(app.querySelectorAll(".poll-chart-row")).toHaveLength(2);
    root.dispatchEvent(new CustomEvent("next:removed", { bubbles: true }));
    expect(app.querySelectorAll(".poll-chart-row")).toHaveLength(0);
    mountChart(root);
    await Promise.resolve();
    expect(app.querySelectorAll(".poll-chart-row")).toHaveLength(2);
    root.remove();
  });

  it("unmounts islands inside a removed subtree", async () => {
    const wrapper = document.createElement("section");
    const root = chartRoot();
    wrapper.append(root);
    document.body.append(wrapper);
    mountChart(root);
    await Promise.resolve();
    const app = root.querySelector("[data-poll-chart-app]");
    expect(app.querySelectorAll(".poll-chart-row")).toHaveLength(2);
    wrapper.dispatchEvent(new CustomEvent("next:removed", { bubbles: true }));
    expect(app.querySelectorAll(".poll-chart-row")).toHaveLength(0);
    wrapper.remove();
  });

  it("leaves mounted islands alone when an unrelated node is removed", async () => {
    const root = chartRoot();
    const other = document.createElement("aside");
    document.body.append(root, other);
    mountChart(root);
    await Promise.resolve();
    other.dispatchEvent(new CustomEvent("next:removed", { bubbles: true }));
    const app = root.querySelector("[data-poll-chart-app]");
    expect(app.querySelectorAll(".poll-chart-row")).toHaveLength(2);
    root.remove();
    other.remove();
  });

  it("ignores next:removed with a non-element target", () => {
    expect(() =>
      document.dispatchEvent(new CustomEvent("next:removed", { bubbles: true })),
    ).not.toThrow();
  });
});

describe("poll deadline", () => {
  it("uses the stream clock and closes voting at the deadline", async () => {
    const root = chartRoot();
    root.dataset.pollDeadline = "4000";
    document.body.append(root);
    mountChart(root);
    await Promise.resolve();
    expect(root.querySelector("[data-poll-countdown]").textContent).toBe(
      "Voting closes in 3 seconds",
    );

    serverNow = 4500;
    phaseUpdated({ is_open: true, deadline: 4000 });

    expect(root.querySelector("[data-poll-status]").textContent).toBe(
      "Voting is closed",
    );
    expect(root.querySelector("[data-poll-countdown]").textContent).toBe("");
    expect(root.querySelector('button[type="submit"]').disabled).toBe(true);
    root.dispatchEvent(new CustomEvent("next:removed", { bubbles: true }));
    root.remove();
  });
});

describe("vote count announcements", () => {
  it("announces the count event to assistive technology", () => {
    document.body.innerHTML = "<p data-poll-vote-announcement></p>";
    voteCountUpdated({ total_votes: 12 });
    expect(document.querySelector("[data-poll-vote-announcement]").textContent).toBe(
      "12 total votes",
    );
  });

  it("ignores invalid counts", () => {
    document.body.innerHTML = "<p data-poll-vote-announcement>5 total votes</p>";
    voteCountUpdated({ total_votes: "12" });
    expect(document.querySelector("[data-poll-vote-announcement]").textContent).toBe(
      "5 total votes",
    );
  });
});

describe("watcher count", () => {
  it("shows the attendance of the poll topic", () => {
    document.body.innerHTML = "<p data-poll-watchers hidden></p>";
    const root = chartRoot();
    mountChart(root);
    presenceUpdated({ count: 2 });
    const label = document.querySelector("[data-poll-watchers]");
    expect(label.textContent).toBe("2 watching");
    expect(label.hidden).toBe(false);
    presenceUpdated({ count: 1 });
    expect(label.textContent).toBe("1 watching");
  });

  it("ignores a snapshot without a numeric count", () => {
    document.body.innerHTML = "<p data-poll-watchers hidden></p>";
    mountChart(chartRoot());
    presenceUpdated({ count: "2" });
    expect(document.querySelector("[data-poll-watchers]").hidden).toBe(true);
  });

  it("copes with a page that has no watcher label", () => {
    document.body.innerHTML = "";
    mountChart(chartRoot());
    expect(() => presenceUpdated({ count: 2 })).not.toThrow();
  });

  it("drops both subscriptions when the island leaves the document", () => {
    const root = chartRoot();
    document.body.append(root);
    mountChart(root);
    unsubscribe.mockClear();
    root.dispatchEvent(new CustomEvent("next:removed", { bubbles: true }));
    expect(unsubscribe).toHaveBeenCalledTimes(2);
    root.remove();
  });
});
