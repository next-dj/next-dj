import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import PollChart from "./component.vue";

const SNAPSHOT = {
  poll_id: 1,
  total_votes: 5,
  choices: [
    { id: 10, text: "Tabs", votes: 3 },
    { id: 11, text: "Spaces", votes: 2 },
  ],
};

describe("PollChart", () => {
  it("renders one row per choice with vote counts and total", () => {
    const wrapper = mount(PollChart, { props: { snapshot: SNAPSHOT } });
    const rows = wrapper.findAll(".poll-chart-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain("Tabs");
    expect(rows[0].find("[data-poll-chart-votes]").text()).toBe("3");
    expect(rows[1].text()).toContain("Spaces");
    expect(rows[1].find("[data-poll-chart-votes]").text()).toBe("2");
    expect(wrapper.find("[data-poll-chart-total]").text()).toBe("5");
  });

  it("renders an empty chart when mounted without a snapshot", () => {
    const wrapper = mount(PollChart);
    expect(wrapper.findAll(".poll-chart-row")).toHaveLength(0);
    expect(wrapper.find("[data-poll-chart-total]").text()).toBe("0");
  });

  it("rebinds choices and total when applySnapshot pushes a fresh snapshot", async () => {
    const wrapper = mount(PollChart, { props: { snapshot: SNAPSHOT } });
    wrapper.vm.applySnapshot({
      poll_id: 1,
      total_votes: 10,
      choices: [
        { id: 10, text: "Tabs", votes: 7 },
        { id: 11, text: "Spaces", votes: 3 },
      ],
    });
    await wrapper.vm.$nextTick();
    expect(wrapper.find("[data-poll-chart-total]").text()).toBe("10");
    const tabsRow = wrapper.find('[data-choice-id="10"]');
    expect(tabsRow.find("[data-poll-chart-votes]").text()).toBe("7");
    expect(tabsRow.attributes("data-just-updated")).toBe("true");
  });

  it("ignores an empty applySnapshot payload", async () => {
    const wrapper = mount(PollChart, { props: { snapshot: SNAPSHOT } });
    wrapper.vm.applySnapshot(null);
    await wrapper.vm.$nextTick();
    expect(wrapper.find("[data-poll-chart-total]").text()).toBe("5");
    expect(wrapper.findAll(".poll-chart-row")).toHaveLength(2);
  });

  it("computes zero percent and no pulse when no votes have landed", () => {
    const wrapper = mount(PollChart, {
      props: {
        snapshot: {
          poll_id: 1,
          total_votes: 0,
          choices: [{ id: 10, text: "Tabs", votes: 0 }],
        },
      },
    });
    const bar = wrapper.find(".poll-chart-bar");
    expect(bar.attributes("style")).toContain("width: 0%");
    expect(wrapper.find(".poll-chart-row").attributes("data-just-updated")).toBe(
      "false",
    );
  });

  it("keeps the total when a snapshot omits total_votes", async () => {
    const wrapper = mount(PollChart, { props: { snapshot: SNAPSHOT } });
    wrapper.vm.applySnapshot({
      poll_id: 1,
      choices: [{ id: 10, text: "Tabs", votes: 9 }],
    });
    await wrapper.vm.$nextTick();
    expect(wrapper.find("[data-poll-chart-total]").text()).toBe("5");
    expect(wrapper.find('[data-choice-id="10"] [data-poll-chart-votes]').text()).toBe(
      "9",
    );
  });
});
