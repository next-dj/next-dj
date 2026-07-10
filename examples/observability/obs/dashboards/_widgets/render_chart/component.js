/* Chart.js bar chart mounted on `#render-chart-canvas`.
 *
 * Reads `window.Next.context.render_rates` produced by the matching
 * `@component.context` callable. No serializer override here, so the
 * payload is flat. Chart.js is declared at the page level in
 * `stats/page.py` because page-level scripts land in the injection
 * order before this co-located file, so `window.Chart` is already
 * defined when the onMount replay runs.
 */
const charts = new WeakMap();

function mountChart(canvas) {
  if (charts.has(canvas)) {
    return;
  }
  const data = window.Next.context.render_rates;
  if (!data || !Array.isArray(data.bars)) {
    return;
  }

  const palette = {
    pages: "#0f172a",
    components: "#0369a1",
    actions: "#15803d",
  };

  const labels = data.bars.map(function (b) {
    return b.name;
  });
  const values = data.bars.map(function (b) {
    return b.value;
  });
  const colors = labels.map(function (name) {
    return palette[name] || "#475569";
  });

  const chart = new window.Chart(canvas, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [
        {
          label: "Renders in window " + data.window,
          data: values,
          backgroundColor: colors,
          borderRadius: 6,
        },
      ],
    },
    options: {
      indexAxis: "y",
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { enabled: true },
      },
      scales: {
        x: {
          beginAtZero: true,
          ticks: { precision: 0 },
          grid: { color: "rgba(15, 23, 42, 0.05)" },
        },
        y: {
          grid: { display: false },
        },
      },
    },
  });
  charts.set(canvas, chart);
}

Next.partial.onMount("#render-chart-canvas", mountChart);

document.addEventListener("next:removed", function (event) {
  const node = event.target;
  if (!(node instanceof Element)) {
    return;
  }
  const canvases = node.matches("#render-chart-canvas")
    ? [node]
    : node.querySelectorAll("#render-chart-canvas");
  for (const canvas of canvases) {
    const chart = charts.get(canvas);
    if (chart !== undefined) {
      chart.destroy();
      charts.delete(canvas);
    }
  }
});
