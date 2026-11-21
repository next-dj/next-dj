import type { Target } from "./apply";
import type { Bounds } from "./morph";
import { blank } from "./protocol";
import type { HistoryHost, Keeper, Layout } from "./router";
import { createSwap, persist, retire, sheets, slice } from "./swap";

type Snapshot = [
  chain: Layout[],
  target: Target,
  content: HTMLTemplateElement,
  title: string,
  sheets: string[],
  context: Record<string, unknown>,
  at: number,
];
type Row = [key: string, offsets: number[]];

const SCROLL = "nx:scroll";
const FOREIGN = "nx:foreign";

export function createHistory(
  host: HistoryHost,
  listening: AddEventListenerOptions = {},
): Omit<Keeper, "take" | "prefetch" | "drop" | "form"> {
  const { partial } = host;
  const doc = document;
  const snapshots = new Map<string, Snapshot>();
  const plain = new Set<string>();
  const bound = (): number => host.settings().snapshots ?? 10;
  const foreign = (add?: string): string[] => {
    try {
      const raw = add === "" ? "[]" : sessionStorage.getItem(FOREIGN);
      const list = new Set(JSON.parse(raw ?? "[]") as string[]);
      if (add !== undefined) {
        if (add) list.add(add);
        sessionStorage.setItem(FOREIGN, JSON.stringify([...list].slice(-50)));
      }
      return [...list];
    } catch {
      return [];
    }
  };

  const regions = (): Element[] => [
    ...doc.querySelectorAll("[data-next-scroll-region]"),
  ];
  const scroll = (key: string, value?: number[]): number[] | undefined => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(SCROLL) ?? "[]") as Row[];
      const rows = saved.filter((row) => row[0] !== key || !value);
      if (value) {
        const kept = [...rows, [key, value]];
        kept.splice(0, kept.length - bound());
        sessionStorage.setItem(SCROLL, JSON.stringify(kept));
      }
      return rows.find((row) => row[0] === key)?.[1];
    } catch {
      return undefined;
    }
  };
  const save = (key: string): unknown =>
    scroll(key, [scrollY, ...regions().map((el) => el.scrollTop)]);
  const place = (key: string): boolean => {
    const saved = scroll(key);
    if (!saved) return true;
    const [y = 0, ...offsets] = saved;
    regions().forEach((el, index) => (el.scrollTop = offsets[index] ?? el.scrollTop));
    scrollTo(0, y);
    return scrollY >= y;
  };

  const store = (key: string, target: Target, bounds: Bounds): void => {
    snapshots.delete(key);
    const context = Object.entries(host.context()).filter(
      ([name]) => !name.startsWith("$"),
    );
    snapshots.set(key, [
      host.chain(),
      target,
      slice(bounds),
      doc.title,
      sheets(),
      Object.fromEntries(context),
      Date.now(),
    ]);
    for (const old of snapshots.keys())
      if (snapshots.size > bound()) snapshots.delete(old);
  };

  addEventListener("pagehide", () => save(host.key()), listening);
  addEventListener(
    "pageshow",
    (event) => {
      if (event.persisted) snapshots.clear();
    },
    listening,
  );
  place(host.key());

  return {
    ...createSwap(host),
    flush() {
      snapshots.clear();
      foreign("");
    },
    foreign: (path) => foreign().includes(path),
    remember: (path) => void foreign(path),
    plain: (path) => plain.has(path),
    share: (path) => void plain.add(path),
    leave(key, target) {
      save(key);
      const bounds = target && host.region(target);
      if (target && bounds) store(key, target, bounds);
    },
    pop(from, to, keep, owner) {
      save(from);
      const saved = snapshots.get(to);
      const bounds = saved && host.region(saved[1]);
      if (!saved || !bounds) return 0;
      const [chain, target, content, title, kept, context, time] = saved;
      if (keep) store(from, target, bounds);
      const current = host.chain();
      const at = (list: Layout[]): number =>
        list.findIndex((layout) => layout[0] === target.slot) + 1;
      const [before, done] = persist(bounds);
      doc.title = title;
      partial.apply(
        {
          version: "",
          ops: [
            { op: "context", data: blank({ ...context }, host.context()) },
            { op: "morph", target, html: content.innerHTML },
          ],
        },
        { before, done, owner },
      );
      host.chain([...current.slice(0, at(current)), ...chain.slice(at(chain))]);
      retire(kept);
      place(to);
      return time;
    },
    place,
    top(hash) {
      const target = hash ? doc.getElementById(hash.slice(1)) : null;
      if (target) target.scrollIntoView();
      else scrollTo(0, 0);
      return !hash || target !== null;
    },
  };
}
