import { beforeEach, describe, expect, it } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import PollChart from "./component.vue";

const constructed = [];
let lastSource = null;

class TrackedEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.closed = false;
    constructed.push(url);
    lastSource = this;
  }
  addEventListener(type, fn) {
    (this.listeners[type] ??= []).push(fn);
  }
  emit(type, payload) {
    for (const fn of this.listeners[type] ?? []) {
      fn({ data: JSON.stringify(payload) });
    }
  }
  close() {
    this.closed = true;
  }
}

beforeEach(() => {
  constructed.length = 0;
  lastSource = null;
  window.Next = {
    context: {
      results: {
        poll_id: 1,
        stream_url: "/polls/1/stream/",
        total_votes: 5,
        choices: [
          { id: 10, text: "Tabs", votes: 3 },
          { id: 11, text: "Spaces", votes: 2 },
        ],
      },
    },
  };
  window.EventSource = TrackedEventSource;
});

describe("PollChart", () => {
  it("renders one row per choice with vote counts and total", () => {
    const wrapper = mount(PollChart);
    const rows = wrapper.findAll(".poll-chart-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain("Tabs");
    expect(rows[0].find("[data-poll-chart-votes]").text()).toBe("3");
    expect(rows[1].text()).toContain("Spaces");
    expect(rows[1].find("[data-poll-chart-votes]").text()).toBe("2");
    expect(wrapper.find("[data-poll-chart-total]").text()).toBe("5");
  });

  it("subscribes to the configured stream URL on mount", () => {
    mount(PollChart);
    expect(constructed).toEqual(["/polls/1/stream/"]);
    expect(lastSource).not.toBeNull();
  });

  it.each([["snapshot"], ["update"]])(
    "rebinds choices and total when a %s event arrives",
    async (eventName) => {
      const wrapper = mount(PollChart);
      lastSource.emit(eventName, {
        poll_id: 1,
        total_votes: 10,
        choices: [
          { id: 10, text: "Tabs", votes: 7 },
          { id: 11, text: "Spaces", votes: 3 },
        ],
      });
      await flushPromises();
      expect(wrapper.find("[data-poll-chart-total]").text()).toBe("10");
      const tabsRow = wrapper.find('[data-choice-id="10"]');
      expect(tabsRow.find("[data-poll-chart-votes]").text()).toBe("7");
      expect(tabsRow.attributes("data-just-updated")).toBe("true");
    },
  );

  it("closes the EventSource when the component unmounts", () => {
    const wrapper = mount(PollChart);
    wrapper.unmount();
    expect(lastSource.closed).toBe(true);
  });

  it.each([
    [
      "empty-context-object",
      () => {
        window.Next.context = {};
      },
    ],
    [
      "null-results",
      () => {
        window.Next.context.results = null;
      },
    ],
    [
      "undefined-Next",
      () => {
        delete window.Next;
      },
    ],
  ])(
    "renders empty list and skips EventSource when context is unavailable (%s)",
    (_label, mutate) => {
      mutate();
      const wrapper = mount(PollChart);
      expect(wrapper.findAll(".poll-chart-row")).toHaveLength(0);
      expect(wrapper.find("[data-poll-chart-total]").text()).toBe("0");
      expect(constructed).toHaveLength(0);
    },
  );
});
