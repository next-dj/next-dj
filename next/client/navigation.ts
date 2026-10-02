// The address bar and its next:navigated event. A layer pushes its URL as it opens,
// so Back closes it while the body loads, and the push is announced once the envelope
// answering it commits. Every write inside one commit folds into a single event.

import { defaultHistory } from "./adapters";
import type { HistoryAdapter } from "./apply";
import { normTitle } from "./head";
import { currentUrl, fire } from "./protocol";
import type { PartialError } from "./protocol";

export type NavigationAction = "push" | "replace" | "pop" | "none";

/** Where the page stands, the shape Next.navigation.current() returns. */
export interface NavigationState {
  url: string;
  path: string;
  title: string;
}

/** The next:navigated detail, a title-only change carrying the action none. */
export interface NavigatedDetail extends NavigationState {
  action: NavigationAction;
}

/** One history write, from a url op or a layer open. */
export interface Intent {
  href: string;
  action: "push" | "replace";
}

/** An open commit, the writes of one envelope announced once at end. */
export interface Commit {
  /** Announce the push held for the page the envelope answers. */
  claim(owner: string | undefined): void;
  write(intent: Intent): void;
  /** Announce the change the commit made, if any. */
  end(): void;
}

export interface Navigation {
  current(): NavigationState;
  /**
   * Push href at once and hold its announcement until an envelope for owner commits.
   *
   * The cancel drops the hold and, while the bar still shows owner, replaces it back,
   * neither write announced.
   */
  hold(owner: string, href: string, onCommit: () => void): () => void;
  begin(): Commit;
  /** The history seam over write, for a caller that only knows push and replace. */
  asHistory(): HistoryAdapter;
  /** Announce a Back gesture after the work it triggers. */
  popped(during: () => void): void;
}

export interface NavigationDeps {
  dispatch: (event: string, detail: Record<string, unknown>) => void;
  document?: Document;
  history?: HistoryAdapter;
}

interface Move {
  action?: NavigationAction;
  from: NavigationState;
}

export function createNavigation(deps: NavigationDeps): Navigation {
  const doc = deps.document ?? document;
  const history = deps.history ?? defaultHistory();
  const held = new Map<string, { from: NavigationState; onCommit: () => void }>();
  // The path last announced, the from-point of a Back the browser already applied.
  let known = currentUrl(doc);
  let open: Move | undefined;

  function current(): NavigationState {
    return {
      url: doc.location.href,
      path: currentUrl(doc),
      title: normTitle(doc.title),
    };
  }

  // A throwing pushState (a rate limit) costs the entry, never the envelope.
  function record(intent: Intent): boolean {
    try {
      history[intent.action](intent.href);
      return true;
    } catch (error) {
      fire(doc, deps.dispatch, "partial:error", {
        kind: "op",
        op: "url",
        error,
      } satisfies PartialError);
      return false;
    }
  }

  // A push anywhere in the commit is a new entry, whatever replaced it after.
  function moved(move: Move, action: NavigationAction): void {
    move.action = move.action === "push" ? "push" : action;
  }

  function frame(preset: Move): Commit {
    const nested = open !== undefined;
    const move = open ?? preset;
    open = move;
    return {
      claim(owner) {
        const entry = owner === undefined ? undefined : held.get(owner);
        if (owner === undefined || entry === undefined) return;
        held.delete(owner);
        // The push already moved the bar, so the change is measured from before it.
        move.from = entry.from;
        moved(move, "push");
        entry.onCommit();
      },
      write(intent) {
        if (record(intent)) moved(move, intent.action);
      },
      end() {
        if (nested) return;
        open = undefined;
        const from = move.from;
        const to = current();
        known = to.path;
        const urlChanged = to.path !== from.path;
        if (!urlChanged && to.title === from.title) return;
        fire(doc, deps.dispatch, "next:navigated", {
          ...to,
          action: urlChanged ? (move.action ?? "push") : "none",
        } satisfies NavigatedDetail);
      },
    };
  }

  // Write and announce at once, or fold into the commit already open.
  function write(href: string, action: Intent["action"]): void {
    const commit = frame({ from: current() });
    commit.write({ href, action });
    commit.end();
  }

  return {
    current,
    hold(owner, href, onCommit) {
      const entry = { from: current(), onCommit };
      if (!record({ href, action: "push" })) return () => undefined;
      held.set(owner, entry);
      return () => {
        if (held.get(owner) !== entry) return;
        held.delete(owner);
        if (currentUrl(doc) !== owner) return;
        record({ href: entry.from.path, action: "replace" });
        // An unannounced push rolls back unannounced, so an open commit starts here.
        if (open !== undefined) open.from = current();
      };
    },
    begin: () => frame({ from: current() }),
    asHistory: () => ({
      push: (href) => write(href, "push"),
      replace: (href) => write(href, "replace"),
    }),
    popped(during) {
      const commit = frame({ action: "pop", from: { ...current(), path: known } });
      try {
        during();
      } finally {
        commit.end();
      }
    },
  };
}
