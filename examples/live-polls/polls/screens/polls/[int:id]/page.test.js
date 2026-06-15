import { beforeEach, describe, expect, it, vi } from "vitest";

let mountChart;

beforeEach(async () => {
  vi.resetModules();
  mountChart = null;
  window.Next = {
    partial: {
      onMount: (selector, callback) => {
        expect(selector).toBe("[data-poll-chart]");
        mountChart = callback;
      },
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
});
