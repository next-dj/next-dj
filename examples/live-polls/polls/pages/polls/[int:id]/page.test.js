import { beforeEach, describe, expect, it, vi } from "vitest";

let mountChart;
let contextUpdated;

beforeEach(async () => {
  vi.resetModules();
  mountChart = null;
  contextUpdated = null;
  window.Next = {
    partial: {
      onMount: (selector, callback) => {
        expect(selector).toBe("[data-poll-chart]");
        mountChart = callback;
      },
    },
    on: (event, callback) => {
      expect(event).toBe("context-updated");
      contextUpdated = callback;
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
  root.innerHTML = `
    <div data-poll-chart-data data-total-votes="${total}" hidden>${items}</div>
    <div data-poll-chart-app id="poll-chart-app" data-next-keep></div>
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
