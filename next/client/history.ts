// The history of the router: slot snapshots for Back, scroll positions per entry, and
// the paths a page refused to answer as an envelope.

import type { MorphPatch, Target } from "./apply";
import type { Layout } from "./layout";
import { between } from "./morph";
import type { Bounds } from "./morph";
import { ATTR_SCROLL_REGION, blank, matching } from "./protocol";
import type { HistoryHost, Turn } from "./router";
import { createSwap, persist, retire, sheets, slice } from "./swap";

/** What the router keeps of the pages a visitor left and how it lands the next one. */
export interface History {
  /** Save the scroll of the entry left and snapshot its slot when target is given. */
  leave(key: string, target?: Target): void;
  /** Put back the snapshot of entry to on Back, answering when it was taken. */
  pop(from: string, to: string, keep: boolean, owner: string): number | undefined;
  /** Restore the saved scroll of an entry, false while the page is still too short. */
  place(key: string): boolean;
  /** Scroll to a fragment or the top, false while the fragment is not on the page. */
  top(hash: string): boolean;
  flush(): void;
  swap(
    record: Record<string, unknown>,
    op: MorphPatch,
    bounds: Bounds,
    kind: string,
  ): Turn;
  extract(html: string, href: string): Record<string, unknown>;
  /** Whether a path answered no envelope, so the router leaves it to the browser. */
  foreign(path: string): boolean;
  remember(path: string): void;
  /** Whether a path is a shared page a navigation fetches as a plain document. */
  plain(path: string): boolean;
  share(path: string): void;
  focus(nodes: readonly Element[]): boolean;
  announce(text: string): void;
}

type Field = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;

// The slot a visit left, enough to put the page back on Back without a request.
interface Snapshot {
  chain: Layout[];
  target: Target;
  content: HTMLTemplateElement;
  title: string;
  sheets: string[];
  context: Record<string, unknown>;
  at: number;
}

// The scroll offsets of one entry, the window first and each scroll region after it.
type Row = [key: string, offsets: number[]];

const SCROLL = "nx:scroll";
const FOREIGN = "nx:foreign";
const FIELDS = "input:not([type=file]),textarea,select";
const TOGGLES = /^(checkbox|radio)$/;

// A snapshot travels as markup, which keeps no typed value, so each field of the copy
// takes what the user typed into the attribute the restore reads back. The live fields
// are read, as a copy keeps no selected option.
function freeze(bounds: Bounds, content: HTMLTemplateElement): HTMLTemplateElement {
  const copies = content.content.querySelectorAll<Field>(FIELDS);
  between(bounds)
    .flatMap((node) => matching(node, FIELDS) as Field[])
    .forEach((live, index) => {
      const copy = copies.item(index);
      if (live instanceof HTMLSelectElement)
        [...live.options].forEach((option, at) =>
          (copy as HTMLSelectElement).options
            .item(at)
            ?.toggleAttribute("selected", option.selected),
        );
      else if (live instanceof HTMLTextAreaElement) copy.textContent = live.value;
      else if (TOGGLES.test(live.type)) copy.toggleAttribute("checked", live.checked);
      else copy.setAttribute("value", live.value);
    });
  return content;
}

export function createHistory(
  host: HistoryHost,
  listening: AddEventListenerOptions = {},
): History {
  const { partial } = host;
  const doc = document;
  const snapshots = new Map<string, Snapshot>();
  const plain = new Set<string>();
  const bound = (): number => host.settings().snapshots ?? 10;
  const foreign = ((): Set<string> => {
    try {
      return new Set(JSON.parse(sessionStorage.getItem(FOREIGN) ?? "[]") as string[]);
    } catch {
      return new Set();
    }
  })();
  const write = (): void => {
    try {
      sessionStorage.setItem(FOREIGN, JSON.stringify([...foreign]));
    } catch {
      // A full or blocked storage only costs the next page this list.
    }
  };

  const regions = (): Element[] => [...doc.querySelectorAll(`[${ATTR_SCROLL_REGION}]`)];
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
    snapshots.set(key, {
      chain: host.chain(),
      target,
      content: freeze(bounds, slice(bounds)),
      title: doc.title,
      sheets: sheets(),
      context: Object.fromEntries(context),
      at: Date.now(),
    });
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

  return {
    ...createSwap(host),
    flush() {
      snapshots.clear();
      foreign.clear();
      write();
    },
    foreign: (path) => foreign.has(path),
    remember(path) {
      foreign.delete(path);
      foreign.add(path);
      for (const old of foreign) if (foreign.size > 50) foreign.delete(old);
      write();
    },
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
      const bounds = saved && host.region(saved.target);
      if (!saved || !bounds) return undefined;
      const { chain, target, content, title, sheets: kept, context, at } = saved;
      if (keep) store(from, target, bounds);
      const current = host.chain();
      const depth = (list: Layout[]): number =>
        list.findIndex((layout) => layout[0] === target.slot) + 1;
      const [before, placePersisted] = persist(bounds);
      doc.title = title;
      partial.apply(
        {
          version: "",
          ops: [
            { op: "context", data: blank({ ...context }, host.context()) },
            { op: "morph", target, html: content.innerHTML },
          ],
        },
        { before, done: placePersisted, owner },
      );
      host.chain([...current.slice(0, depth(current)), ...chain.slice(depth(chain))]);
      retire(kept);
      place(to);
      return at;
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
