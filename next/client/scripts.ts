// Third-party scripts from the $scripts manifest, gated by consent and loaded at once,
// on idle, on the first interaction, or on demand. A tag the server already rendered
// is not inserted again.

import { asString, cssEscape, fire, isRecord } from "./protocol";

export type ScriptStatus =
  | "rendered"
  | "pending"
  | "loading"
  | "loaded"
  | "error"
  | "blocked";

/** Run a callback once the page idles, at the latest after IDLE_TIMEOUT_MS. */
export type IdleAdapter = (run: () => void) => void;

/** The public Next.scripts surface plus the runtime's own seams. */
export interface Scripts {
  load(name: string): Promise<void>;
  status(name: string): ScriptStatus | undefined;
  /** Register the $scripts manifest entries. */
  _configure(value: unknown): void;
  /** Re-check the blocked entries after a consent change. */
  _refresh(): void;
}

export interface ScriptsDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  allows: (category: string) => boolean;
  document?: Document;
  idle?: IdleAdapter;
  // Where the first interaction is listened for. Absent, the window.
  events?: EventTarget;
  // The CSP nonce of the runtime script, set on every inserted script.
  nonce?: string | undefined;
}

interface Entry {
  name: string;
  src: string | undefined;
  init: string | undefined;
  strategy: string;
  category: string;
  attrs: [string, string][];
}

interface Waiter {
  resolve: () => void;
  reject: (error: Error) => void;
}

interface Entrant {
  entry: Entry;
  status: ScriptStatus;
  waiters: Waiter[];
}

const ATTR_SCRIPT = "data-next-script";
const IDLE_TIMEOUT_MS = 3000;
const INTERACTIONS = ["pointerdown", "keydown", "touchstart", "scroll"];
// A dynamically inserted script is async by default. These strategies set async to
// false, so scripts that waited for consent run in insertion order.
const IN_ORDER = new Set(["blocking", "defer"]);

function readEntry(value: unknown): Entry | undefined {
  if (!isRecord(value)) return undefined;
  const name = asString(value.name);
  const src = asString(value.src);
  const init = asString(value.init);
  if (name === undefined || (src === undefined && init === undefined)) return undefined;
  const attrs = isRecord(value.attrs)
    ? Object.entries(value.attrs).filter(
        (pair): pair is [string, string] => typeof pair[1] === "string",
      )
    : [];
  return {
    name,
    src,
    init,
    strategy: asString(value.strategy) ?? "async",
    category: asString(value.category) ?? "necessary",
    attrs,
  };
}

// Run after the load event once the main thread idles, bounded by IDLE_TIMEOUT_MS.
function defaultIdle(): IdleAdapter {
  return (run) => {
    const schedule = (): void => {
      // Safari has no requestIdleCallback, so a timeout replaces it.
      if ("requestIdleCallback" in globalThis)
        requestIdleCallback(run, { timeout: IDLE_TIMEOUT_MS });
      else globalThis.setTimeout(run, 1);
    };
    if (document.readyState === "complete") schedule();
    else window.addEventListener("load", schedule, { once: true });
  };
}

export function createScripts(deps: ScriptsDeps): Scripts {
  const doc = deps.document ?? document;
  const idle = deps.idle ?? defaultIdle();
  const entrants = new Map<string, Entrant>();
  const events = deps.events ?? window;
  const waiting: Entrant[] = [];
  let armed = false;

  function rendered(name: string): boolean {
    return doc.querySelector(`script[${ATTR_SCRIPT}="${cssEscape(name)}"]`) !== null;
  }

  // failed names the URL that did not load, absent on success.
  function settle(entrant: Entrant, failed?: string): void {
    const { name } = entrant.entry;
    entrant.status = failed === undefined ? "loaded" : "error";
    if (failed === undefined) fire(doc, deps.dispatch, "next:script-loaded", { name });
    else fire(doc, deps.dispatch, "next:script-error", { name, url: failed });
    for (const waiter of entrant.waiters.splice(0)) {
      if (failed === undefined) waiter.resolve();
      else waiter.reject(new Error(`script ${name} failed to load`));
    }
  }

  function element(entry: Entry): HTMLScriptElement {
    const el = doc.createElement("script");
    for (const [key, value] of entry.attrs) el.setAttribute(key, value);
    if (deps.nonce !== undefined) el.nonce = deps.nonce;
    el.setAttribute(ATTR_SCRIPT, entry.name);
    return el;
  }

  // Marks an entry blocked after a revoke while it waited, and rejects pending loads.
  function block(entrant: Entrant): void {
    entrant.status = "blocked";
    for (const waiter of entrant.waiters.splice(0)) {
      waiter.reject(new Error(`script ${entrant.entry.name} is blocked`));
    }
  }

  // The init body runs first, so a queue stub it defines exists before the vendor runs.
  function start(entrant: Entrant): void {
    if (entrant.status !== "pending") return;
    const { entry } = entrant;
    // Consent may have been revoked while an idle, interaction or manual entry waited.
    if (!deps.allows(entry.category)) {
      block(entrant);
      return;
    }
    entrant.status = "loading";
    if (entry.init !== undefined) {
      const el = element(entry);
      el.textContent = entry.init;
      doc.head.append(el);
    }
    const src = entry.src;
    if (src === undefined) {
      settle(entrant);
      return;
    }
    const el = element(entry);
    el.async = !IN_ORDER.has(entry.strategy);
    el.addEventListener("load", () => settle(entrant), { once: true });
    el.addEventListener("error", () => settle(entrant, src), { once: true });
    el.src = src;
    doc.head.append(el);
  }

  function arm(): void {
    if (armed) return;
    armed = true;
    const controller = new AbortController();
    const release = (): void => {
      controller.abort();
      armed = false;
      for (const entrant of waiting.splice(0)) start(entrant);
    };
    for (const type of INTERACTIONS) {
      events.addEventListener(type, release, {
        capture: true,
        passive: true,
        signal: controller.signal,
      });
    }
  }

  function schedule(entrant: Entrant): void {
    const { entry } = entrant;
    if (!deps.allows(entry.category)) {
      entrant.status = "blocked";
      return;
    }
    entrant.status = "pending";
    if (entry.strategy === "manual") return;
    if (entry.strategy === "idle") {
      idle(() => start(entrant));
    } else if (entry.strategy === "interaction") {
      waiting.push(entrant);
      arm();
    } else {
      start(entrant);
    }
  }

  function load(name: string): Promise<void> {
    const entrant = entrants.get(name);
    const status = entrant?.status ?? (rendered(name) ? "rendered" : undefined);
    if (status === "rendered" || status === "loaded") return Promise.resolve();
    if (entrant === undefined || status === "error" || status === "blocked") {
      return Promise.reject(new Error(`script ${name} is ${status ?? "unknown"}`));
    }
    const done = new Promise<void>((resolve, reject) => {
      entrant.waiters.push({ resolve, reject });
    });
    start(entrant);
    return done;
  }

  return {
    load,
    status: (name) =>
      entrants.get(name)?.status ?? (rendered(name) ? "rendered" : undefined),
    _configure(value) {
      const entries = (Array.isArray(value) ? value : [])
        .map(readEntry)
        .filter((entry): entry is Entry => entry !== undefined);
      for (const entry of entries) {
        if (entrants.has(entry.name)) continue;
        const entrant: Entrant = { entry, status: "rendered", waiters: [] };
        entrants.set(entry.name, entrant);
        if (!rendered(entry.name)) schedule(entrant);
      }
    },
    _refresh() {
      for (const entrant of entrants.values()) {
        const allowed = deps.allows(entrant.entry.category);
        if (entrant.status === "blocked" && allowed) schedule(entrant);
        else if (entrant.status === "pending" && !allowed) block(entrant);
      }
    },
  };
}
