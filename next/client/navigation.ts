// The address bar and its next:navigated event. A history write is held until the
// envelope answering it commits, so a failed or closed layer leaves no entry, and
// every write inside one commit folds into a single event.

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
  /** Write the intent held for the page the envelope answers. */
  claim(owner: string | undefined): void;
  write(intent: Intent): void;
  /** Announce the change the commit made, if any. */
  end(): void;
}

export interface Navigation {
  current(): NavigationState;
  /** Hold a write until an envelope for owner commits it, returning the cancel. */
  hold(owner: string, intent: Intent, onCommit: () => void): () => void;
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
}

export function createNavigation(deps: NavigationDeps): Navigation {
  const doc = deps.document ?? document;
  const history = deps.history ?? defaultHistory();
  const held = new Map<string, { intent: Intent; onCommit: () => void }>();
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
  function record(move: Move, intent: Intent): boolean {
    try {
      history[intent.action](intent.href);
    } catch (error) {
      fire(doc, deps.dispatch, "partial:error", {
        kind: "op",
        op: "url",
        error,
      } satisfies PartialError);
      return false;
    }
    // A push anywhere in the commit is a new entry, whatever replaced it after.
    move.action = move.action === "push" ? "push" : intent.action;
    return true;
  }

  function frame(from: NavigationState, preset: Move): Commit {
    const nested = open !== undefined;
    const move = open ?? preset;
    open = move;
    return {
      claim(owner) {
        const entry = owner === undefined ? undefined : held.get(owner);
        if (owner === undefined || entry === undefined) return;
        held.delete(owner);
        if (record(move, entry.intent)) entry.onCommit();
      },
      write: (intent) => void record(move, intent),
      end() {
        if (nested) return;
        open = undefined;
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
    const commit = frame(current(), {});
    commit.write({ href, action });
    commit.end();
  }

  return {
    current,
    hold(owner, intent, onCommit) {
      const entry = { intent, onCommit };
      held.set(owner, entry);
      return () => {
        if (held.get(owner) === entry) held.delete(owner);
      };
    },
    begin: () => frame(current(), {}),
    asHistory: () => ({
      push: (href) => write(href, "push"),
      replace: (href) => write(href, "replace"),
    }),
    popped(during) {
      const commit = frame({ ...current(), path: known }, { action: "pop" });
      try {
        during();
      } finally {
        commit.end();
      }
    },
  };
}
