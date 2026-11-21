import { defaultReload } from "./adapters";
import type { Envelope } from "./apply";
import type { PartialSurface } from "./partial";
import {
  ATTR_BOUNDARY,
  ATTR_ERROR,
  ATTR_STATE,
  ATTR_TRANSPORT,
  ATTR_ZONE,
  HEADER_ZONE,
  NAVIGATED_FLAG,
  matching,
  streaming,
} from "./protocol";
import type { DeferArm, Watch } from "./triggers";
import type { Outcome, WireRequest } from "./wire";

const TOKEN = "data-next-token";
const WRAPPER = `[${ATTR_BOUNDARY}][${TOKEN}]`;
const ZONE = `[${ATTR_ZONE}]`;

export interface DeferHost {
  partial: Pick<PartialSurface, "fetch" | "layers" | "onMount">;
}

interface BoundaryOp {
  op?: string;
  html?: string;
  target?: { boundary?: string };
  s?: string;
  k?: string;
  token?: string;
}

const dropped = (): Outcome => ({ status: "dropped", applied: false });

const statusOf = ({ error }: Outcome): number =>
  error?.kind === "http" ? error.status : 0;

const rearm = ({ error }: Outcome): { status: string } => ({
  status: error !== undefined && error.kind !== "http" ? "error" : "ok",
});

export function createDefer(
  next: DeferHost,
  listening: AddEventListenerOptions = {},
  reload = defaultReload(),
): DeferArm {
  const doc = document;
  const partial = next.partial;
  const send = partial.fetch.bind(partial);
  const seen = new WeakMap<Element, string | null>();
  const queued: Element[] = [];
  let observe: Watch;
  let armed = false;
  let halted = false;

  function reloadOnce(): boolean {
    try {
      if (sessionStorage.getItem(NAVIGATED_FLAG) !== null) return false;
      sessionStorage.setItem(NAVIGATED_FLAG, "1");
    } catch {
      return false;
    }
    reload();
    return true;
  }

  async function settled(el: Element, sent: WireRequest): Promise<Outcome> {
    const active = doc.activeElement;
    const inside = el.contains(active) && active !== el ? active : null;
    const outcome = await send(sent);
    const target = doc.getElementById(el.id) ?? (el as HTMLElement);
    if (inside !== null && !inside.isConnected) {
      if (!target.hasAttribute("tabindex")) {
        target.setAttribute("tabindex", "-1");
        target.addEventListener("blur", () => target.removeAttribute("tabindex"), {
          once: true,
        });
      }
      target.focus();
    }
    return outcome;
  }

  async function boundary(el: Element, token: string, auto: boolean): Promise<Outcome> {
    const [place, field = "_nb"] = (el.getAttribute(ATTR_TRANSPORT) ?? "").split(":");
    const url = new URL(partial.layers.urlFor(el), location.href);
    const headers: Record<string, string> = { [HEADER_ZONE]: el.id };
    const sent: WireRequest = { url: "", headers, queue: el.id, boundaries: [el] };
    if (place === "query") url.searchParams.set(field, token);
    else if (place === "body") {
      sent.method = "POST";
      sent.abortable = true;
      sent.body = new URLSearchParams({ [field]: token });
    } else headers["X-Next-Boundary"] = token;
    sent.url = url.pathname + url.search;
    const outcome = await settled(el, sent);
    const status = statusOf(outcome);
    if (status === 409) reloadOnce();
    if (status === 403 && !(auto && reloadOnce()))
      el.setAttribute(ATTR_ERROR, "forbidden");
    return outcome;
  }

  function request(el: Element, auto = false): Promise<Outcome> {
    const token = el.getAttribute(TOKEN);
    const zone = el.getAttribute(ATTR_ZONE);
    if (token !== null) return boundary(el, token, auto);
    if (zone === null) return Promise.resolve(dropped());
    return settled(el, { url: partial.layers.urlFor(el), zone });
  }

  function retry(control: Element): void {
    const wrapper = control.closest(`[${ATTR_BOUNDARY}]`);
    const owner = wrapper?.hasAttribute(TOKEN) ? wrapper : control.closest(ZONE);
    if (owner === null) reload();
    else void request(owner);
  }

  function load(el: Element): void {
    if (queued.push(el) > 1) return;
    const flush = (): void =>
      queueMicrotask(() => {
        for (const wrapper of queued.splice(0)) void request(wrapper, true);
      });
    if (doc.readyState === "loading" && !streaming(doc))
      doc.addEventListener("DOMContentLoaded", flush, { ...listening, once: true });
    else flush();
  }

  function incomplete(): void {
    if (halted) return;
    for (const el of matching(doc, `[${ATTR_ERROR}=incomplete]`)) {
      const target = el.hasAttribute(TOKEN) ? el : el.closest(ZONE);
      if (target === null || seen.has(target)) continue;
      seen.set(target, null);
      setTimeout(() => {
        if (halted) return;
        void request(target, true).then((outcome) => {
          const status = statusOf(outcome);
          if (status === 429 || status >= 500) halted = true;
        });
      }, Math.random() * 2000);
    }
  }

  const later = (): void => void setTimeout(incomplete);

  function activate(el: Element): void {
    const mode = el.getAttribute("data-next-defer");
    const token = el.getAttribute(TOKEN);
    if (seen.get(el) === token) return;
    seen.set(el, token);
    if (mode === "load") load(el);
    if (mode === "visible") observe(el, () => request(el, true).then(rearm));
  }

  function eachBoundary(
    event: Event,
    run: (op: BoundaryOp, el: Element) => void,
  ): void {
    const { envelope } = (event as CustomEvent<{ envelope: Envelope }>).detail;
    for (const op of envelope.ops as readonly BoundaryOp[]) {
      const el = doc.getElementById(op.target?.boundary ?? "");
      if (el !== null) run(op, el);
    }
  }

  function carry(op: BoundaryOp, el: Element): void {
    if (op.op !== "morph" || !el.hasAttribute("data-next-adopted")) return;
    const parsed = doc.createElement("template");
    parsed.innerHTML = op.html ?? "";
    const root = parsed.content.firstElementChild;
    if (root === null) return;
    for (const { name, value } of el.attributes) {
      if (/^(id$|data-next-(?!busy))/.test(name) && !root.hasAttribute(name))
        root.setAttribute(name, value);
    }
    op.html = parsed.innerHTML;
  }

  function stamp(op: BoundaryOp, el: Element): void {
    if (op.s !== undefined)
      el.setAttribute(ATTR_STATE, op.s === "error" ? "error" : "ready");
    if (op.s === "error") el.setAttribute(ATTR_ERROR, op.k ?? "error");
    if (op.token === undefined) return;
    el.setAttribute(TOKEN, op.token);
  }

  const onBefore = (event: Event): void => eachBoundary(event, carry);

  const onApplied = (event: Event): void => eachBoundary(event, stamp);

  function onClick(event: Event): void {
    const target = event.target;
    const control =
      target instanceof Element
        ? target.closest("[data-next-fetch],[data-next-retry]")
        : null;
    if (control === null) return;
    const wrapper = control.hasAttribute("data-next-fetch")
      ? control.closest(WRAPPER)
      : control;
    if (wrapper === null) return;
    event.preventDefault();
    if (wrapper === control) retry(control);
    else void request(wrapper);
  }

  return (watch) => {
    observe = watch;
    if (armed) return;
    armed = true;
    listening.signal?.addEventListener("abort", () => (halted = true));
    doc.addEventListener("click", onClick, listening);
    doc.addEventListener("partial:before-apply", onBefore, listening);
    doc.addEventListener("partial:applied", onApplied, listening);
    doc.addEventListener("partial:settled", later, listening);
    addEventListener("load", later, listening);
    partial.fetch = (target) =>
      target instanceof Element ? request(target) : send(target);
    partial.onMount(`${WRAPPER}[${ATTR_STATE}=idle]`, activate);
    incomplete();
  };
}
