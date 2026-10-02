import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createConsent } from "./consent";

interface Dispatched {
  event: string;
  detail: Record<string, unknown>;
}

const CONFIG = {
  cookie: { name: "next_consent", max_age: 15552000, samesite: "Lax" },
  categories: ["necessary", "analytics", "marketing", "preferences"],
  decided: false,
  granted: ["necessary"],
};

function clearCookies(): void {
  for (const pair of document.cookie.split(";")) {
    const name = pair.split("=")[0]!.trim();
    if (name !== "") document.cookie = `${name}=; max-age=0; path=/`;
  }
}

function cookie(name: string): string | undefined {
  const pair = document.cookie
    .split("; ")
    .find((entry) => entry.startsWith(`${name}=`));
  return pair?.slice(name.length + 1);
}

function makeConsent(config: unknown = CONFIG) {
  const dispatched: Dispatched[] = [];
  const reload = vi.fn();
  const onChange = vi.fn();
  const consent = createConsent({
    dispatch: (event, detail) => dispatched.push({ event, detail }),
    reload,
    onChange,
    now: () => 1_700_000_000_500,
  });
  consent._configure(config);
  return { consent, dispatched, reload, onChange };
}

describe("consent state", () => {
  beforeEach(clearCookies);
  afterEach(clearCookies);

  it("grants only necessary until the visitor decides", () => {
    const { consent } = makeConsent();
    expect(consent.decided()).toBe(false);
    expect(consent.get()).toEqual({
      necessary: true,
      analytics: false,
      marketing: false,
      preferences: false,
    });
    expect(Object.isFrozen(consent.get())).toBe(true);
  });

  it("seeds a remembered decision from the payload", () => {
    const { consent } = makeConsent({
      ...CONFIG,
      decided: true,
      granted: ["analytics", "unknown"],
    });
    expect(consent.decided()).toBe(true);
    expect(consent.get()).toMatchObject({ analytics: true, marketing: false });
    expect(consent.get()).not.toHaveProperty("unknown");
  });

  it("keeps necessary when the payload leaves it out", () => {
    const { consent } = makeConsent({ categories: ["analytics"], granted: [] });
    expect(consent.get()).toEqual({ necessary: true, analytics: false });
  });

  it("ignores a payload that is not an object", () => {
    const { consent } = makeConsent("nope");
    expect(consent.get()).toEqual({ necessary: true });
  });
});

describe("consent updates", () => {
  beforeEach(clearCookies);
  afterEach(clearCookies);

  it("writes the decision cookie and announces the change", () => {
    const { consent, dispatched, onChange } = makeConsent();
    const onDocument = vi.fn();
    document.addEventListener("next:consent", onDocument);
    consent.update({ analytics: true, marketing: false });
    document.removeEventListener("next:consent", onDocument);
    expect(cookie("next_consent")).toBe("2:analytics:1700000000");
    expect(consent.decided()).toBe(true);
    expect(dispatched).toEqual([
      {
        event: "next:consent",
        detail: {
          granted: ["necessary", "analytics"],
          denied: ["marketing", "preferences"],
          changed: ["analytics"],
          initial: false,
        },
      },
    ]);
    expect(onDocument).toHaveBeenCalledOnce();
    expect(onChange).toHaveBeenCalledOnce();
  });

  it("keeps a category the choice leaves out and never drops necessary", () => {
    const { consent } = makeConsent();
    consent.update({ analytics: true });
    consent.update({ marketing: true, necessary: false });
    expect(consent.get()).toMatchObject({
      necessary: true,
      analytics: true,
      marketing: true,
    });
    expect(cookie("next_consent")).toBe("2:analytics|marketing:1700000000");
  });

  it("acceptAll and rejectAll flip every category past necessary", () => {
    const { consent, dispatched } = makeConsent();
    consent.acceptAll();
    expect(cookie("next_consent")).toBe("2:analytics|marketing|preferences:1700000000");
    consent.rejectAll();
    expect(cookie("next_consent")).toBe("2::1700000000");
    expect(consent.get()).toMatchObject({ necessary: true, analytics: false });
    expect(dispatched.at(-1)!.detail.changed).toEqual([
      "analytics",
      "marketing",
      "preferences",
    ]);
  });

  it("stays silent when a decided visitor's choice changes nothing", () => {
    document.cookie = "next_consent=1:analytics:1600000000; path=/";
    const { consent, dispatched, onChange } = makeConsent();
    const writes = vi.spyOn(document, "cookie", "set");
    consent.update({ analytics: true, marketing: false });
    expect(writes).not.toHaveBeenCalled();
    expect(dispatched).toEqual([]);
    expect(onChange).not.toHaveBeenCalled();
    consent.update({ marketing: true });
    expect(dispatched).toHaveLength(1);
  });

  it("persists the first decision even when it matches the undecided state", () => {
    const { consent, dispatched } = makeConsent();
    consent.rejectAll();
    expect(consent.decided()).toBe(true);
    expect(cookie("next_consent")).toBe("2::1700000000");
    expect(dispatched).toHaveLength(1);
  });

  it("reloads on request only when the choice changed something", () => {
    const { consent, reload } = makeConsent();
    consent.update({ analytics: false }, { reload: true });
    expect(reload).not.toHaveBeenCalled();
    consent.update({ analytics: true }, { reload: true });
    expect(reload).toHaveBeenCalledOnce();
  });
});

describe("consent cookie attributes", () => {
  beforeEach(clearCookies);
  afterEach(clearCookies);

  function written(config: Record<string, unknown>): string {
    const setter = vi.spyOn(document, "cookie", "set");
    const { consent } = makeConsent({ categories: ["analytics"], cookie: config });
    consent.acceptAll();
    return setter.mock.calls[0]![0];
  }

  it("spells every configured attribute", () => {
    expect(
      written({
        name: "c",
        path: "/app/",
        max_age: 60,
        samesite: "Strict",
        domain: "example.com",
        secure: true,
      }),
    ).toBe(
      "c=2:analytics:1700000000; path=/app/; max-age=60; samesite=Strict; " +
        "domain=example.com; secure",
    );
  });

  it("falls back to the default name, root path, age and the page scheme", () => {
    expect(written({ max_age: null })).toBe(
      "next_consent=2:analytics:1700000000; path=/; max-age=15552000",
    );
  });

  it("an explicit false keeps secure off", () => {
    expect(written({ secure: false })).not.toContain("secure");
  });
});

describe("consent without a payload", () => {
  afterEach(clearCookies);

  it("defaults the clock, reload and document", () => {
    const consent = createConsent({ dispatch: () => undefined });
    consent.acceptAll();
    expect(cookie("next_consent")).toMatch(/^2::\d+$/);
  });

  it("reloads the document it writes to and follows its scheme", () => {
    const writes: string[] = [];
    const reload = vi.fn();
    // jsdom pins location to plain http and cannot reload, so a stub stands in.
    const stub = {
      location: { protocol: "https:", reload },
      get cookie() {
        return "";
      },
      set cookie(value: string) {
        writes.push(value);
      },
      dispatchEvent: () => true,
      querySelectorAll: () => [],
    } as unknown as Document;
    const consent = createConsent({ dispatch: () => undefined, document: stub });
    consent._configure(CONFIG);
    consent.update({ analytics: true }, { reload: true });
    expect(writes).toEqual([expect.stringMatching(/; secure$/)]);
    expect(reload).toHaveBeenCalledOnce();
  });
});

describe("the stored decision", () => {
  beforeEach(clearCookies);
  afterEach(clearCookies);

  it("wins over the undecided state a shared page carries", () => {
    document.cookie = "next_consent=2:analytics|unknown:1700000000; path=/";
    const { consent } = makeConsent();
    expect(consent.decided()).toBe(true);
    expect(consent.get()).toMatchObject({ analytics: true, marketing: false });
    expect(consent.get()).not.toHaveProperty("unknown");
  });

  it("reads the first format, which joins the categories with commas", () => {
    document.cookie = "next_consent=1:analytics,unknown:1700000000; path=/";
    const { consent } = makeConsent();
    expect(consent.decided()).toBe(true);
    expect(consent.get()).toMatchObject({ analytics: true, marketing: false });
  });

  it("reads the cookie the payload names", () => {
    document.cookie = "choice=1::1700000000; path=/";
    const { consent } = makeConsent({
      ...CONFIG,
      decided: true,
      granted: ["analytics"],
      cookie: { name: "choice" },
    });
    expect(consent.get()).toMatchObject({ analytics: false });
  });

  it.each(["3:analytics:1", "2:analytics", "1:analytics", "garbage", ""])(
    "falls back to the payload for a foreign value %j",
    (value) => {
      document.cookie = `next_consent=${value}; path=/`;
      const { consent } = makeConsent({ ...CONFIG, granted: ["marketing"] });
      expect(consent.decided()).toBe(false);
      expect(consent.get()).toMatchObject({ analytics: false, marketing: true });
    },
  );

  it("announces the starting state once, with nothing changed", () => {
    document.cookie = "next_consent=1:analytics:1700000000; path=/";
    const { consent, dispatched } = makeConsent();
    consent._announce();
    expect(dispatched).toEqual([
      {
        event: "next:consent",
        detail: {
          granted: ["necessary", "analytics"],
          denied: ["marketing", "preferences"],
          changed: [],
          initial: true,
        },
      },
    ]);
  });
});

describe("consented markup", () => {
  const BLOCK =
    '<p>before</p><template data-next-consented="marketing">' +
    '<iframe src="https://video.example/x"></iframe></template>' +
    '<a href="/consent/">Allow video</a><!--/next-consented--><p>after</p>';

  beforeEach(() => {
    clearCookies();
    document.body.innerHTML = "";
  });
  afterEach(clearCookies);

  it("stays behind its else branch until the category is granted", () => {
    document.body.innerHTML = BLOCK;
    const { consent } = makeConsent();
    expect(document.querySelector("iframe")).toBeNull();
    expect(document.querySelector("a")).not.toBeNull();
    consent.update({ analytics: true });
    expect(document.querySelector("template")).not.toBeNull();
    consent.update({ marketing: true });
    expect(document.body.innerHTML).toBe(
      '<p>before</p><iframe src="https://video.example/x"></iframe><p>after</p>',
    );
  });

  it("reveals at once for a visitor whose cookie already grants it", () => {
    document.cookie = "next_consent=1:marketing:1700000000; path=/";
    document.body.innerHTML = BLOCK;
    makeConsent();
    expect(document.querySelector("iframe")).not.toBeNull();
    expect(document.querySelector("a")).toBeNull();
  });

  it("runs a script the revealed markup carries", () => {
    document.body.innerHTML =
      '<template data-next-consented="marketing">' +
      '<script data-kind="embed">document.body.dataset.ran = "1"</script>' +
      "</template><!--/next-consented-->";
    const { consent } = makeConsent();
    consent.update({ marketing: true });
    expect(document.body.dataset.ran).toBe("1");
    expect(document.querySelector("script")!.dataset.kind).toBe("embed");
  });

  it("keeps the revealed markup on a revoke without a reload", () => {
    document.body.innerHTML = BLOCK;
    const { consent } = makeConsent();
    consent.update({ marketing: true });
    consent.update({ marketing: false });
    expect(document.querySelector("iframe")).not.toBeNull();
  });

  it("skips a nested block's marker when finding the else branch", () => {
    document.body.innerHTML =
      '<template data-next-consented="marketing"><b>video</b></template>' +
      '<template data-next-consented="analytics"><i>chart</i></template>' +
      "<s>no chart</s><!--/next-consented--><u>no video</u><!--/next-consented-->" +
      "<p>after</p>";
    const { consent } = makeConsent();
    consent.update({ marketing: true });
    expect(document.body.innerHTML).toBe("<b>video</b><p>after</p>");
  });

  it("reveals a block nested inside a revealed one", () => {
    document.body.innerHTML =
      '<template data-next-consented="marketing"><div>' +
      '<template data-next-consented="analytics"><i>chart</i></template>' +
      "<s>no chart</s><!--/next-consented--></div></template><!--/next-consented-->";
    const { consent } = makeConsent();
    consent.acceptAll();
    expect(document.body.innerHTML).toBe("<div><i>chart</i></div>");
  });

  it("leaves the following markup alone when the server sent no end marker", () => {
    document.body.innerHTML =
      '<template data-next-consented="marketing"><b>video</b></template><p>after</p>';
    const { consent } = makeConsent();
    consent.update({ marketing: true });
    expect(document.body.innerHTML).toBe("<b>video</b><p>after</p>");
  });

  it("hands the outermost revealed roots to the mount pass", () => {
    document.body.innerHTML =
      '<template data-next-consented="marketing"><div id="outer">' +
      '<template data-next-consented="analytics"><i id="inner">chart</i></template>' +
      '<!--/next-consented--></div><p id="second"></p></template><!--/next-consented-->';
    const revealed: Element[][] = [];
    const consent = createConsent({
      dispatch: () => undefined,
      onReveal: (nodes) => revealed.push([...nodes]),
    });
    consent._configure(CONFIG);
    consent.acceptAll();
    expect(revealed.at(-1)!.map((node) => node.id)).toEqual(["outer", "second"]);
  });

  it("reads the cookie's own categories on a page whose payload has no $consent", () => {
    document.cookie = "next_consent=1:marketing:1700000000; path=/";
    document.body.innerHTML = BLOCK;
    const { consent } = makeConsent(null);
    expect(consent.get()).toEqual({ necessary: true, marketing: true });
    expect(document.querySelector("iframe")).not.toBeNull();
  });

  it("reads a cookie granting nothing past necessary without a payload", () => {
    document.cookie = "next_consent=1::1700000000; path=/";
    const { consent } = makeConsent(null);
    expect(consent.get()).toEqual({ necessary: true });
    expect(consent.decided()).toBe(true);
  });

  it("reveals markup a later patch brought in on request", () => {
    const { consent } = makeConsent();
    consent.update({ marketing: true });
    document.body.innerHTML = BLOCK;
    consent._reveal([document.body]);
    expect(document.querySelector("iframe")).not.toBeNull();
  });

  it("reveals only inside the roots it is handed", () => {
    const { consent } = makeConsent();
    consent.update({ marketing: true });
    document.body.innerHTML = `<div id="kept">${BLOCK}</div><div id="patched">${BLOCK}</div>`;
    consent._reveal([document.getElementById("patched")!]);
    expect(document.querySelector("#kept template")).not.toBeNull();
    expect(document.querySelector("#patched iframe")).not.toBeNull();
  });

  it("reveals a root that is a consented block itself", () => {
    const { consent } = makeConsent();
    consent.update({ marketing: true });
    document.body.innerHTML = BLOCK;
    const block = document.querySelector("template")!;
    consent._reveal([block, document.querySelector("a")!]);
    expect(document.body.innerHTML).toBe(
      '<p>before</p><iframe src="https://video.example/x"></iframe><p>after</p>',
    );
  });
});
