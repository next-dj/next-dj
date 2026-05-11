/* React sparkline mounted on `#sparkline-mount`.
 *
 * Reads `window.Next.context.totals_chart` produced by the matching
 * `@component.context` callable. The wrapper override means the
 * payload is shaped as `{v, data}`, so the bars live under
 * `.data.bars`. The component renders one rounded bar per source
 * scaled to the largest value in the snapshot.
 *
 * Babel-standalone fetches and transforms `<script type="text/babel">`
 * tags after `DOMContentLoaded` already fired, so a fresh listener on
 * that event would never run. The init code dispatches on
 * `document.readyState` instead, which works both during initial
 * compilation and on hot reloads.
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

const mountSparkline = () => {
  const mount = document.getElementById("sparkline-mount");
  if (!mount || !window.React || !window.ReactDOM || !window.Next || !window.Next.context) {
    return;
  }
  const envelope = window.Next.context.totals_chart;
  const bars = envelope && envelope.data && Array.isArray(envelope.data.bars)
    ? envelope.data.bars
    : [];
  const root = window.ReactDOM.createRoot(mount);
  root.render(<Sparkline bars={bars} />);
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountSparkline);
} else {
  mountSparkline();
}
