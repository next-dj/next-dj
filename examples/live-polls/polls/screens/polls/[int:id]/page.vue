<script>
import { createApp } from "vue";
import PollChart from "./_widgets/poll_chart/component.vue";

// The stream sends a `refresh` of the `poll-results` zone, so the chart
// lives through the same re-render as the SSR bars. `Next.partial.onMount`
// runs this over the initial DOM and over the morphed zone after every
// refresh. The visible bars sit in a `data-next-keep` container the Vue
// app owns, so the morph never fights Vue for those nodes. The fresh
// per-choice counts ride in the sibling `data-poll-chart-data` block the
// morph does update, and each pass reads them and pushes the snapshot
// into the live instance.
//
// The voter's own tab takes a shorter path. Its vote response carries a
// `context` patch beside the zone morph, so `context-updated` fires with
// `window.Next.context.live_results` already holding the fresh snapshot.
// The listener pushes that straight into the live instance, no DOM re-read.

const instances = new WeakMap();

function readSnapshot(root) {
  const data = root.querySelector("[data-poll-chart-data]");
  if (!data) return null;
  const choices = Array.from(data.querySelectorAll("[data-choice-id]")).map((el) => ({
    id: Number(el.dataset.choiceId),
    text: el.dataset.choiceText ?? "",
    votes: Number(el.dataset.choiceVotes ?? 0),
  }));
  return {
    poll_id: Number(root.dataset.pollChart),
    total_votes: Number(data.dataset.totalVotes ?? 0),
    choices,
  };
}

function mountChart(root) {
  const snapshot = readSnapshot(root);
  const existing = instances.get(root);
  if (existing) {
    existing.applySnapshot(snapshot);
    return;
  }
  const target = root.querySelector("[data-poll-chart-app]");
  if (!target) return;
  target.innerHTML = "";
  const vm = createApp(PollChart, { snapshot }).mount(target);
  instances.set(root, vm);
}

function applyContextSnapshot() {
  const snapshot = window.Next?.context?.live_results;
  if (!snapshot) return;
  for (const root of document.querySelectorAll("[data-poll-chart]")) {
    const vm = instances.get(root);
    if (vm) vm.applySnapshot(snapshot);
  }
}

window.Next?.partial?.onMount("[data-poll-chart]", mountChart);
window.Next?.on?.("context-updated", applyContextSnapshot);

export default {};
</script>
