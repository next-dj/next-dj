import { defaultMove } from "./adapters";
import type { Asset } from "./apply";
import type { ScriptsChunk, ScriptsModule } from "./chunks";
import { focusable } from "./events";
import { markers } from "./layout";
import type { Layout } from "./layout";
import { between, fireRemoved } from "./morph";
import type { Bounds, Move } from "./morph";
import { ATTR_ASSET, cssEscape, matching, uid } from "./protocol";
import type { HistoryHost, Keeper } from "./router";

type Sheet = HTMLLinkElement | HTMLStyleElement;

const PERSIST = "data-next-persist";
const RUN = "data-next-run";
const PAGE = `[${ATTR_ASSET}=page]`;
const SHEETS = "link[rel=stylesheet],style";
const SOURCES = "link[rel=stylesheet][href],script[src]";
const LAYOUTS = /"\$layouts":(\[(?:\[[^\]]*\],?)*\])/;
const RUNS = /^\s*$|module|script/i;

const named = (el: Sheet): string =>
  el instanceof HTMLLinkElement ? el.href : el.textContent;

const keyed = (asset: Asset): string =>
  asset.inline ?? new URL(asset.url, document.baseURI).href;

const nonceOf = (el: Element): string => el.getAttribute("nonce") ?? "";

const source = (el: Element): Record<string, unknown> =>
  el instanceof HTMLLinkElement
    ? { kind: "css", url: el.getAttribute("href"), scope: el.getAttribute(ATTR_ASSET) }
    : {
        kind: el.getAttribute("type") === "module" ? "module" : "js",
        url: el.getAttribute("src"),
      };

export const sheets = (): string[] =>
  [...document.querySelectorAll<Sheet>(PAGE)]
    .filter((el) => !(el.sheet ?? el).disabled)
    .map(named);

export function retire(kept: readonly string[]): void {
  for (const el of document.querySelectorAll<Sheet>(PAGE))
    (el.sheet ?? el).disabled = !kept.includes(named(el));
}

export function slice([parent, start, end]: Bounds): HTMLTemplateElement {
  const range = parent.ownerDocument.createRange();
  range.selectNodeContents(parent);
  if (start) range.setStartAfter(start);
  if (end) range.setEndBefore(end);
  const box = parent.ownerDocument.createElement("template");
  box.content.append(range.cloneContents());
  return box;
}

export function persist(
  bounds: Bounds,
  move: Move = defaultMove,
): [() => void, () => void] {
  const doc = bounds[0].ownerDocument;
  const held = new Map<string, Element>();
  const box = doc.createElement("div");
  const drop = (el: Element): void => {
    fireRemoved(el);
    el.remove();
  };
  return [
    () => {
      const all = between(bounds).flatMap((node) => matching(node, `[${PERSIST}]`));
      for (const el of all) {
        const name = String(el.getAttribute(PERSIST));
        if (!held.has(name) && !all.some((outer) => outer !== el && outer.contains(el)))
          held.set(name, el);
      }
      if (held.size > 0) doc.documentElement.append(box);
      for (const el of held.values()) move(box, el, null);
    },
    () => {
      for (const [name, el] of held) {
        const spot = bounds[0].querySelector(`[${PERSIST}="${cssEscape(name)}"]`);
        if (spot?.parentNode) {
          move(spot.parentNode, el, spot);
          drop(spot);
        } else drop(el);
      }
      box.remove();
    },
  ];
}

export function createSwap(
  host: HistoryHost,
): Pick<Keeper, "swap" | "extract" | "focus" | "announce"> {
  const doc = document;
  const { nonce } = host;
  let live: HTMLElement | undefined;
  return {
    focus: (nodes) => {
      const pick = (selector: string): Element | undefined =>
        nodes.flatMap((node) => matching(node, selector))[0];
      const target = pick("[data-next-focus]") ?? pick("h1") ?? nodes[0];
      if (!(target instanceof HTMLElement)) return false;
      focusable(target).focus({ preventScroll: true });
      return doc.activeElement === target;
    },
    announce: (text) => {
      if (!live?.isConnected) {
        live = doc.createElement("div");
        live.setAttribute("aria-live", "polite");
        live.style.cssText = "position:fixed;clip-path:inset(50%)";
        doc.body.append(live);
      }
      const box = live;
      box.textContent = "";
      setTimeout(() => (box.textContent = text), 50);
    },
    swap(record, op, bounds, motion) {
      const { scripts, consent } = record;
      const sent = record.nonce;
      const runs: HTMLScriptElement[] = [];
      const mark = uid();
      const [rescue, place] = persist(bounds);
      let finish: () => void;
      const finished = new Promise<void>((resolve) => (finish = resolve));
      if (typeof op.html === "string" && typeof sent === "string" && sent) {
        const box = doc.createElement("template");
        box.innerHTML = op.html;
        for (const script of box.content.querySelectorAll("script"))
          if (
            nonceOf(script) === sent &&
            !script.hasAttribute("src") &&
            RUNS.test(script.type)
          ) {
            const spot = doc.createElement("template");
            spot.setAttribute(RUN, `${mark}${runs.push(script)}`);
            script.replaceWith(spot);
          }
        op.html = box.innerHTML;
      }
      return {
        hold: (hold) => {
          if (
            !host.settings().transitions ||
            !("startViewTransition" in doc) ||
            matchMedia("(prefers-reduced-motion: reduce)").matches
          )
            return;
          const release = hold();
          const update = (): Promise<void> => {
            release();
            return finished;
          };
          doc.documentElement.setAttribute("data-next-transition", motion);
          try {
            doc.startViewTransition({ update, types: [motion] });
          } catch {
            doc.startViewTransition(update);
          }
        },
        before: rescue,
        after: (envelope, nodes, ok) => {
          place();
          if (ok) {
            runs.forEach((old, index) => {
              const spot = doc.querySelector(`template[${RUN}="${mark}${index + 1}"]`);
              if (!spot) return;
              const fresh = doc.createElement("script");
              if (/module/i.test(old.type)) fresh.type = "module";
              if (nonce) fresh.nonce = nonce;
              fresh.textContent = old.textContent;
              spot.replaceWith(fresh);
            });
            const keys = envelope.assets.map(keyed);
            for (const el of doc.querySelectorAll<Sheet>(SHEETS)) {
              const scope = envelope.assets[keys.indexOf(named(el))]?.scope;
              if (scope) el.setAttribute(ATTR_ASSET, scope);
            }
            retire(keys);
            const configure = (chunk: ScriptsChunk): void => {
              const loaded = chunk as ScriptsModule;
              loaded.consent._configure(consent);
              loaded.scripts._configure(scripts);
              loaded.consent._reveal(nodes);
            };
            if (scripts ?? consent)
              void host.ready?.("scripts").then(configure, () => undefined);
          }
          finish();
        },
      };
    },
    extract(html, href) {
      const parsed = new DOMParser().parseFromString(html, "text/html");
      const inits = [...parsed.scripts].filter((el) => el.text.includes("Next._init("));
      const own = [...new Set(inits.map(nonceOf))];
      const trusted = own.length < 2 && !(nonce && !own[0]) ? (own[0] ?? "") : null;
      const assets: Record<string, unknown>[] = [];
      for (const el of parsed.querySelectorAll(SOURCES)) {
        if (
          el.closest("noscript") ||
          (el instanceof HTMLScriptElement && !RUNS.test(el.type))
        )
          continue;
        if (nonceOf(el) !== trusted) return {};
        assets.push(source(el));
      }
      const fresh = JSON.parse(LAYOUTS.exec(html)?.[1] ?? "[]") as Layout[];
      const chain = host.chain();
      const id = (layout?: Layout): string => (layout ? layout[0] + layout[1] : "");
      let kept = 0;
      while (kept < fresh.length && id(chain[kept]) === id(fresh[kept])) kept += 1;
      const at = fresh[kept - 1];
      const found = at && markers(parsed, at[0], at[2]);
      let target: Record<string, unknown> = { body: true };
      let region: Bounds = [parsed.body, null, null];
      if (at && found) {
        target = { slot: at[0] };
        region = found;
      } else kept = 0;
      return {
        version: "",
        ops: [
          { op: "morph", target, html: slice(region).innerHTML },
          { op: "url", href },
          { op: "meta", title: parsed.title },
        ],
        assets,
        layouts: fresh.map(([name, digest, seal], index) => [
          name,
          digest,
          index < kept ? null : seal,
        ]),
      };
    },
  };
}
