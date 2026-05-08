/* Chart.js bar chart mounted on `#render-chart-canvas`.
 *
 * Reads `window.Next.context.render_rates` produced by the matching
 * `@component.context` callable and draws one horizontal bar per
 * source. Chart.js arrives from a CDN URL listed in `scripts = [...]`
 * inside `component.py`. The framework collects and dedupes that URL
 * through the static collector. The init runs on `DOMContentLoaded`
 * so the chart waits for every collected script (Chart.js included)
 * to finish parsing before drawing.
 */
document.addEventListener("DOMContentLoaded", function () {
  const canvas = document.getElementById("render-chart-canvas");
  if (!canvas || !window.Chart || !window.Next || !window.Next.context) {
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

  const labels = data.bars.map(function (b) { return b.name; });
  const values = data.bars.map(function (b) { return b.value; });
  const colors = labels.map(function (name) { return palette[name] || "#475569"; });

  new window.Chart(canvas, {
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
});
