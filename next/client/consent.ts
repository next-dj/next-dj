// The visitor's consent per category, read from the consent cookie and written back
// on every choice. $consent seeds the state when there is no cookie, and a page in a
// shared cache carries the undecided state. Only the visitor's choice grants more
// than the necessary category.

import { asString, fire, isRecord, matching } from "./protocol";

const NECESSARY = "necessary";
const DEFAULT_COOKIE = "next_consent";
// The server's default cookie age, used when the payload sets none, since a session
// cookie would lose the choice when the browser closes.
const DEFAULT_MAX_AGE = 15552000;
const CONSENTED = "data-next-consented";
const CONSENTED_TEMPLATES = `template[${CONSENTED}]`;
// The comment that ends the else branch following a consented template.
const CONSENTED_END = "/next-consented";

/** The next:consent detail. initial is true for the state the page loads with. */
export interface ConsentChange {
  granted: string[];
  denied: string[];
  changed: string[];
  initial: boolean;
}

/** The public Next.consent surface plus the chunk's own seams. */
export interface Consent {
  get(): Readonly<Record<string, boolean>>;
  decided(): boolean;
  update(choice: Record<string, boolean>, opts?: { reload?: boolean }): void;
  acceptAll(): void;
  rejectAll(): void;
  /** Seed the state from the cookie, or from the $consent payload without a cookie. */
  _configure(value: unknown): void;
  /** Announce the initial state once the chunk is configured. */
  _announce(): void;
  /** Reveal the consented markup in roots the granted categories allow. */
  _reveal(roots: readonly ParentNode[]): void;
}

export interface ConsentDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  document?: Document;
  reload?: () => void;
  now?: () => number;
  // Called after every update, so gated scripts activate without a reload.
  onChange?: () => void;
  // Called with the revealed roots, so they get the mount pass patched markup gets.
  onReveal?: (nodes: readonly Element[]) => void;
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

// The categories a stored decision grants, undefined for a missing or unknown value.
// Version 2 joins them with "|", and the legacy version 1 with ",".
function storedChoice(jar: string, name: string): string[] | undefined {
  const prefix = `${name}=`;
  const pair = jar.split("; ").find((entry) => entry.startsWith(prefix));
  const match = /^([12]):([^:]*):[^:]*$/.exec(pair?.slice(prefix.length) ?? "");
  return match?.[2]?.split(match[1] === "1" ? "," : "|");
}

// The nodes after a consented template up to and including its end marker, skipping
// the markers of nested blocks. Without a marker there is no else branch.
function deniedBranch(template: Element): ChildNode[] {
  const branch: ChildNode[] = [];
  let depth = 0;
  for (let node = template.nextSibling; node !== null; node = node.nextSibling) {
    if (node.nodeType === Node.COMMENT_NODE && node.textContent === CONSENTED_END) {
      if (depth === 0) return [...branch, node];
      depth -= 1;
    } else if (node instanceof HTMLTemplateElement && node.hasAttribute(CONSENTED)) {
      depth += 1;
    }
    branch.push(node);
  }
  return [];
}

export function createConsent(deps: ConsentDeps): Consent {
  const doc = deps.document ?? document;
  const reload = deps.reload ?? (() => doc.location.reload());
  const now = deps.now ?? (() => Date.now());
  let categories = [NECESSARY];
  let granted = new Set(categories);
  let decided = false;
  let cookie: Record<string, unknown> = {};

  const cookieName = (): string => asString(cookie.name) ?? DEFAULT_COOKIE;

  // Writes the version, the granted categories but necessary, and the time in seconds.
  function persist(): void {
    const list = categories.filter((c) => c !== NECESSARY && granted.has(c)).join("|");
    const maxAge =
      typeof cookie.max_age === "number" ? cookie.max_age : DEFAULT_MAX_AGE;
    const parts = [
      `${cookieName()}=2:${list}:${Math.floor(now() / 1000)}`,
      `path=${asString(cookie.path) ?? "/"}`,
      `max-age=${maxAge}`,
    ];
    const sameSite = asString(cookie.samesite);
    if (sameSite !== undefined) parts.push(`samesite=${sameSite}`);
    const domain = asString(cookie.domain);
    if (domain !== undefined) parts.push(`domain=${domain}`);
    // An unset flag follows the page scheme, so a plain-HTTP dev server keeps it.
    const secure =
      typeof cookie.secure === "boolean"
        ? cookie.secure
        : doc.location.protocol === "https:";
    if (secure) parts.push("secure");
    doc.cookie = parts.join("; ");
  }

  // The next:consent detail for the current state.
  function state(changed: string[], initial: boolean): ConsentChange {
    return {
      granted: categories.filter((c) => granted.has(c)),
      denied: categories.filter((c) => !granted.has(c)),
      changed,
      initial,
    };
  }
  // Replaces the else branch of each block whose category is granted with the
  // template body. A revoke leaves revealed markup in place until the next page load.
  // The pass runs in document order. A block revealed inside another is added to the
  // pass and mounted with its outer block, and one inside a removed else branch is
  // skipped. A root may itself be a block, the top of a replaced fragment.
  function reveal(within: readonly ParentNode[]): void {
    const roots: Element[] = [];
    const work = within.flatMap((root) =>
      matching(root, CONSENTED_TEMPLATES).map((template) => ({
        template,
        nested: false,
      })),
    );
    for (const { template, nested } of work) {
      if (
        !template.isConnected ||
        !granted.has(String(template.getAttribute(CONSENTED)))
      )
        continue;
      for (const node of deniedBranch(template)) node.remove();
      const body = doc.importNode((template as HTMLTemplateElement).content, true);
      for (const inner of Array.from(body.querySelectorAll(CONSENTED_TEMPLATES))) {
        work.push({ template: inner, nested: true });
      }
      if (!nested) roots.push(...Array.from(body.children));
      template.replaceWith(body);
    }
    deps.onReveal?.(roots);
  }

  function update(
    choice: Record<string, boolean>,
    opts: { reload?: boolean } = {},
  ): void {
    const before = granted;
    const next = new Set([NECESSARY]);
    for (const category of categories) {
      const wanted = choice[category];
      const on = typeof wanted === "boolean" ? wanted : before.has(category);
      if (on) next.add(category);
    }
    const changed = categories.filter((c) => before.has(c) !== next.has(c));
    // A banner that repeats an unchanged decision changes and announces nothing.
    if (decided && changed.length === 0) return;
    granted = next;
    decided = true;
    persist();
    reveal([doc]);
    fire(doc, deps.dispatch, "next:consent", { ...state(changed, false) });
    deps.onChange?.();
    // A revoked script keeps running until the page unloads, so only a reload stops it.
    if (opts.reload === true && changed.length > 0) reload();
  }

  function all(value: boolean): Record<string, boolean> {
    return Object.fromEntries(categories.map((c) => [c, value]));
  }

  return {
    get: () =>
      Object.freeze(Object.fromEntries(categories.map((c) => [c, granted.has(c)]))),
    decided: () => decided,
    update,
    acceptAll: () => update(all(true)),
    rejectAll: () => update(all(false)),
    // A payload without $consent lists no categories, so the stored decision supplies
    // them. With neither, the current state is kept.
    _configure(value) {
      const config = isRecord(value) ? value : undefined;
      if (config !== undefined) {
        cookie = isRecord(config.cookie) ? config.cookie : {};
        const listed = strings(config.categories);
        categories = listed.includes(NECESSARY) ? listed : [NECESSARY, ...listed];
        granted = new Set([
          NECESSARY,
          ...strings(config.granted).filter((c) => categories.includes(c)),
        ]);
        decided = config.decided === true;
      }
      const kept = storedChoice(doc.cookie, cookieName());
      if (kept !== undefined) {
        if (config === undefined) {
          categories = [...new Set([...categories, ...kept.filter((c) => c !== "")])];
        }
        granted = new Set([NECESSARY, ...kept.filter((c) => categories.includes(c))]);
        decided = true;
      }
      reveal([doc]);
    },
    _announce() {
      fire(doc, deps.dispatch, "next:consent", { ...state([], true) });
    },
    _reveal: reveal,
  };
}
