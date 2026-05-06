<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

const ctx = window.Next?.context?.results ?? null;

const choices = ref(ctx ? [...ctx.choices] : []);
const totalVotes = ref(ctx ? ctx.total_votes : 0);
const lastChangedId = ref(null);

const rows = computed(() => {
  const total = totalVotes.value;
  return choices.value.map((choice) => ({
    ...choice,
    percent: total > 0 ? Math.round((choice.votes / total) * 100) : 0,
    justUpdated: choice.id === lastChangedId.value,
  }));
});

let source = null;

function applySnapshot(payload) {
  if (!payload) return;
  const next = payload.choices ?? [];
  const previous = new Map(choices.value.map((c) => [c.id, c.votes]));
  let changed = null;
  for (const choice of next) {
    if ((previous.get(choice.id) ?? 0) !== choice.votes) {
      changed = choice.id;
      break;
    }
  }
  choices.value = next;
  totalVotes.value = payload.total_votes ?? totalVotes.value;
  lastChangedId.value = changed;
}

onMounted(() => {
  if (!ctx?.stream_url) return;
  source = new EventSource(ctx.stream_url);
  source.addEventListener("snapshot", (event) => {
    applySnapshot(JSON.parse(event.data));
  });
  source.addEventListener("update", (event) => {
    applySnapshot(JSON.parse(event.data));
  });
});

onBeforeUnmount(() => {
  if (source) {
    source.close();
    source = null;
  }
});
</script>

<template>
  <ul class="space-y-3">
    <li
      v-for="row in rows"
      :key="row.id"
      class="poll-chart-row"
      :data-choice-id="row.id"
      :data-just-updated="row.justUpdated ? 'true' : 'false'"
    >
      <div class="flex items-center justify-between text-sm">
        <span class="font-medium text-slate-900">{{ row.text }}</span>
        <span class="font-mono text-xs text-slate-500">
          <span data-poll-chart-votes>{{ row.votes }}</span> votes
        </span>
      </div>
      <div class="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-100">
        <div
          class="poll-chart-bar h-full bg-slate-900"
          :style="{ width: row.percent + '%' }"
        />
      </div>
    </li>
  </ul>
  <p class="mt-3 text-xs text-slate-500">
    Total votes so far: <span data-poll-chart-total>{{ totalVotes }}</span>
  </p>
</template>
