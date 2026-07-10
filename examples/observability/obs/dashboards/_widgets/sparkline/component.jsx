/* React sparkline mounted on `#sparkline-mount`.
 *
 * Reads `window.Next.context.totals_chart` produced by the matching
 * `@component.context` callable. The wrapper override means the
 * payload is shaped as `{v, data}`, so the bars live under
 * `.data.bars`. The component renders one rounded bar per source
 * scaled to the largest value in the snapshot.
 *
 * Babel-standalone transforms and runs this file after
 * DOMContentLoaded, so React, ReactDOM, and the runtime globals are
 * already in place when `Next.partial.onMount` replays its callback
 * over the parsed document.
 */
const Sparkline = ({ bars }) => {
  if (!Array.isArray(bars) || bars.length === 0) {
    return <p className="text-sm text-slate-500">No data yet. Click around the dashboard to populate.</p>;
  }
  const palette = { pages: "#0f172a", components: "#0369a1", actions: "#15803d" };
  const max = Math.max(1, ...bars.map((b) => b.value));
  return (
    <ul className="space-y-2">
      {bars.map((bar) => (
        <li key={bar.name} className="text-xs">
          <div className="flex items-center justify-between">
            <span className="font-medium capitalize">{bar.name}</span>
            <span className="tabular-nums text-slate-500">{bar.value}</span>
          </div>
          <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-slate-100">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.max(2, (bar.value / max) * 100)}%`,
                backgroundColor: palette[bar.name] || "#475569",
              }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
};

const roots = new WeakMap();

Next.partial.onMount("#sparkline-mount", (mount) => {
  if (roots.has(mount)) {
    return;
  }
  const envelope = window.Next.context.totals_chart;
  const bars = envelope && envelope.data && Array.isArray(envelope.data.bars)
    ? envelope.data.bars
    : [];
  const root = window.ReactDOM.createRoot(mount);
  root.render(<Sparkline bars={bars} />);
  roots.set(mount, root);
});

document.addEventListener("next:removed", (event) => {
  const node = event.target;
  if (!(node instanceof Element)) {
    return;
  }
  const islands = node.matches("#sparkline-mount")
    ? [node]
    : node.querySelectorAll("#sparkline-mount");
  for (const mount of islands) {
    const root = roots.get(mount);
    if (root !== undefined) {
      root.unmount();
      roots.delete(mount);
    }
  }
});
