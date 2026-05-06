<script>
import { createApp } from "vue";
import PollChart from "../_widgets/poll_chart/component.vue";

// A plain `<script>` block runs once when Vite loads this entry as
// an ES module. `<script setup>` would be the setup function of the
// page component, which the bundler never instantiates because no
// outer code calls `createApp` on the page itself. The mount has to
// happen at module top level.
const ctx = window.Next?.context?.results;
if (ctx && ctx.poll_id !== undefined) {
  const root = document.querySelector(`[data-poll-chart="${ctx.poll_id}"]`);
  const target = root?.querySelector("[data-poll-chart-app]");
  if (target) {
    target.innerHTML = "";
    createApp(PollChart).mount(target);
  }
}

export default {};
</script>
