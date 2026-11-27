// The slot swap of a soft visit: persisted nodes carried across, the scripts of the new
// slot run, the stylesheets of the left page retired, and a document carved into an
// envelope when the answer is plain markup.

import { defaultMove } from "./adapters";
import type { Asset } from "./apply";
import type { ScriptsChunk, ScriptsModule } from "./chunks";
import type { History } from "./history";
import { focusable } from "./events";
import { layoutsOf, markers } from "./layout";
import type { Layout } from "./layout";
import { between, fireRemoved } from "./morph";
import type { Bounds, Move } from "./morph";
import {
  ATTR_ASSET,
  ATTR_FOCUS,
  ATTR_INIT,
  ATTR_PERSIST,
  ATTR_RUN,
  ATTR_TRANSITION,
  cssEscape,
  matching,
  uid,
} from "./protocol";
import type { HistoryHost } from "./router";

type Sheet = HTMLLinkElement | HTMLStyleElement;

const PERSIST = ATTR_PERSIST;
const RUN = ATTR_RUN;
const PAGE = `[${ATTR_ASSET}=page]`;
const SHEETS = "link[rel=stylesheet],style";
const SOURCES = "link[rel=stylesheet][href],script[src]";
// The script types a browser runs, anything else being a data block.
const RUNS = /^\s*$|module|script/i;

// A stylesheet's identity, its URL or the text of an inline sheet.
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

// The page stylesheets in force, the ones a snapshot turns back on when it is restored.
export const sheets = (): string[] =>
  [...document.querySelectorAll<Sheet>(PAGE)]
    .filter((el) => !(el.sheet ?? el).disabled)
    .map(named);

// A page stylesheet the next page does not list is disabled, not removed, so Back can
// turn it on again without a request.
export function retire(kept: readonly string[]): void {
  for (const el of document.querySelectorAll<Sheet>(PAGE))
    (el.sheet ?? el).disabled = !kept.includes(named(el));
}

// A copy of what lies between the bounds, the content a snapshot keeps.
export function slice([parent, start, end]: Bounds): HTMLTemplateElement {
  const range = parent.ownerDocument.createRange();
  range.selectNodeContents(parent);
  if (start) range.setStartAfter(start);
  if (end) range.setEndBefore(end);
  const box = parent.ownerDocument.createElement("template");
  box.content.append(range.cloneContents());
  return box;
}

// Lift every outermost `data-next-persist` node out before the swap and set each back
// in place of its namesake in the new slot, so a playing video or a typed form survives.
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
): Pick<History, "swap" | "extract" | "focus" | "announce"> {
  const doc = document;
  const { nonce } = host;
  let live: HTMLElement | undefined;
  return {
    focus: (nodes) => {
      const pick = (selector: string): Element | undefined =>
        nodes.flatMap((node) => matching(node, selector))[0];
      const target = pick(`[${ATTR_FOCUS}]`) ?? pick("h1") ?? nodes[0];
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
    // An inline script of the new slot runs only when it carries the nonce the answer
    // named, so markup injected into the page cannot smuggle one in.
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
          doc.documentElement.setAttribute(ATTR_TRANSITION, motion);
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
              void host.scripts().then(configure, () => undefined);
          }
          finish();
        },
      };
    },
    // Carve a plain document into an envelope: its sources only when every init script
    // shares one nonce, and the slot of the deepest layout both chains have in common.
    extract(html, href) {
      const parsed = new DOMParser().parseFromString(html, "text/html");
      const inits = [...parsed.querySelectorAll(`script[${ATTR_INIT}]`)];
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
      const fresh = layoutsOf(parsed);
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
      // A plain document names no nonce, so the swap would run none of its inline
      // scripts but the init, and the page loads instead, as it would without the router.
      const content = slice(region);
      for (const el of content.content.querySelectorAll<HTMLScriptElement>(
        "script:not([src])",
      ))
        if (RUNS.test(el.type) && !el.closest(`noscript,[${ATTR_INIT}]`)) return {};
      return {
        version: "",
        ops: [
          { op: "morph", target, html: content.innerHTML },
          { op: "url", href },
          { op: "meta", title: parsed.title },
        ],
        assets,
        layouts: fresh.map(([name, digest, responseId], index) => [
          name,
          digest,
          index < kept ? null : responseId,
        ]),
      };
    },
  };
}
