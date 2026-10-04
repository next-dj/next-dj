// History writes and the next:navigated event. A layer pushes its URL when it opens,
// so Back closes it while the body loads, and the push is announced once the body
// envelope is applied. All writes inside one commit produce a single event.

import { defaultHistory } from "./adapters";
import type { HistoryAdapter } from "./apply";
import { normTitle } from "./head";
import { currentUrl, fire } from "./protocol";
import type { PartialError } from "./protocol";

export type NavigationAction = "push" | "replace" | "pop" | "none";

/** The current URL, path and title, as Next.navigation.current() returns them. */
export interface NavigationState {
  url: string;
  path: string;
  title: string;
}

/** The next:navigated detail. A change of the title alone has the action none. */
export interface NavigatedDetail extends NavigationState {
  action: NavigationAction;
}

/** One history write, from a url op or a layer open. */
export interface Intent {
  href: string;
  action: "push" | "replace";
}

/** The history writes of one envelope, announced together by end. */
export interface Commit {
  /** Include the held push of owner, the page the envelope was fetched for. */
  claim(owner: string | undefined): void;
  write(intent: Intent): void;
  /** Announce the change the commit made, if any. */
  end(): void;
}

export interface Navigation {
  current(): NavigationState;
  /**
   * Push href now and defer its announcement until an envelope for owner is applied.
   *
   * The returned cancel drops the hold and, while the URL is still owner, replaces it
   * with the previous URL. Neither write is announced.
   */
  hold(owner: string, href: string, onCommit: () => void): () => void;
  begin(): Commit;
  /** A HistoryAdapter whose writes are announced like any other. */
  asHistory(): HistoryAdapter;
  /** Run the popstate handling in during, then announce the change as a pop. */
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
  // The path last announced. A popstate is measured from it, since the browser has
  // already changed the location.
  let known = currentUrl(doc);
  let open: Move | undefined;

  function current(): NavigationState {
    return {
      url: doc.location.href,
      path: currentUrl(doc),
      title: normTitle(doc.title),
    };
  }

  // A pushState that throws (a browser rate limit) loses the entry and is reported.
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

  // A push anywhere in the commit makes it a push, even when a replace follows.
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
        // The URL changed with the held push, so start from the state saved before it.
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

  // Write and announce now, or add the write to the commit that is already open.
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
        // The push and its revert are both unannounced, so an open commit starts here.
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
