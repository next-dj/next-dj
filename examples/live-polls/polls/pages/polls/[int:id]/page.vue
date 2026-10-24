<script>
import { createApp } from "vue";
import PollChart from "./_components/poll_chart/component.vue";

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
// `{ context, changed }` where `changed` lists the keys of that delta.
// The listener acts only when `live_results` is in `changed` and pushes
// the merged snapshot straight into the live instance, no DOM re-read.
// It never sees the initial seed — this deferred bundle subscribes after
// the classic inline `Next._init` has already dispatched it, and only
// `ready` replays to late subscribers. The chart's first data comes from
// the DOM snapshot `mountChart` reads, the listener handles only later
// deltas.

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
    existing.vm.applySnapshot(snapshot);
    renderPollPhase(root, existing);
    return;
  }
  const target = root.querySelector("[data-poll-chart-app]");
  if (!target) return;
  target.innerHTML = "";
  const app = createApp(PollChart, { snapshot });
  const vm = app.mount(target);
  const stream = window.Next?.partial?.stream;
  let entry;
  const unsubscribe = stream?.on(
    `poll:${root.dataset.pollChart}`,
    "poll:phase",
    ({ is_open, deadline }) => {
      root.dataset.pollOpen = String(is_open === true);
      root.dataset.pollDeadline = deadline == null ? "" : String(deadline);
      renderPollPhase(root, entry);
    },
  );
  entry = { app, vm, timer: null, unsubscribe };
  instances.set(root, entry);
  renderPollPhase(root, entry);
}

function renderPollPhase(root, entry) {
  const stream = window.Next?.partial?.stream;
  const now = stream?.now?.() ?? Date.now();
  const deadlineValue = root.dataset.pollDeadline;
  const deadline = deadlineValue ? Number(deadlineValue) : null;
  const open = root.dataset.pollOpen === "true";
  const remaining = deadline === null ? null : deadline - now;
  const accepting = open && (remaining === null || remaining > 0);
  const status = root.querySelector("[data-poll-status]");
  const countdown = root.querySelector("[data-poll-countdown]");

  if (status) {
    const message = accepting ? "Voting is open" : "Voting is closed";
    if (status.textContent !== message) status.textContent = message;
  }
  if (countdown) {
    countdown.textContent = accepting && remaining !== null
      ? `Voting closes in ${Math.ceil(remaining / 1000)} seconds`
      : "";
  }
  for (const button of root.querySelectorAll('button[type="submit"]')) {
    button.disabled = !accepting;
  }
  if (open && deadline !== null) {
    if (entry?.timer === null) {
      entry.timer = window.setInterval(() => renderPollPhase(root, entry), 1000);
    }
  } else if (entry && entry.timer !== null) {
    window.clearInterval(entry.timer);
    entry.timer = null;
  }
}

function applyContextSnapshot({ context, changed }) {
  if (!changed.includes("live_results")) return;
  const snapshot = context.live_results;
  if (!snapshot) return;
  for (const root of document.querySelectorAll("[data-poll-chart]")) {
    const entry = instances.get(root);
    if (entry) entry.vm.applySnapshot(snapshot);
  }
}

function announceVoteCount({ total_votes }) {
  if (!Number.isInteger(total_votes)) return;
  const status = document.querySelector("[data-poll-vote-announcement]");
  if (status) status.textContent = `${total_votes} total votes`;
}

// Zone morphs never detach the keep container, so this fires only when
// the zone or the page itself leaves the document. The event lands on
// the detached root, not on each descendant, hence the subtree walk.
function unmountRemoved(event) {
  const node = event.target;
  if (!(node instanceof Element)) return;
  const islands = node.matches("[data-poll-chart]")
    ? [node]
    : node.querySelectorAll("[data-poll-chart]");
  for (const root of islands) {
    const entry = instances.get(root);
    if (entry !== undefined) {
      entry.app.unmount();
      if (entry.timer !== null) window.clearInterval(entry.timer);
      entry.unsubscribe?.();
      instances.delete(root);
    }
  }
}

window.Next?.partial?.onMount("[data-poll-chart]", mountChart);
window.Next?.on?.("context-updated", applyContextSnapshot);
window.Next?.on?.("poll:votes", announceVoteCount);
document.addEventListener("next:removed", unmountRemoved);

export default {};
</script>
