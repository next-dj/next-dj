// The entry point of next.scripts.min.js, consent and third-party scripts. The runtime
// fetches it on demand, and on evaluation it registers its factory with Next._land.

import type { Extras, ExtrasHost } from "./chunks";
import { createConsent } from "./consent";
import { createScripts } from "./scripts";

/** Build the consent and scripts surfaces over the runtime functions in host. */
export function createExtrasChunk(host: ExtrasHost): Extras {
  const consent = createConsent({
    dispatch: host.dispatch,
    onChange: () => scripts._refresh(),
    onReveal: host.mount,
  });
  const scripts = createScripts({
    dispatch: host.dispatch,
    // A joint category lists several names separated by spaces and requires all.
    allows: (category) =>
      category.split(" ").every((name) => consent.get()[name] === true),
    nonce: host.nonce,
  });
  // Consented markup inserted by a patch lies inside the nodes the apply touched.
  document.addEventListener("partial:applied", (event) =>
    consent._reveal((event as CustomEvent<{ nodes: Element[] }>).detail.nodes),
  );
  return {
    consent,
    scripts,
    configure(context) {
      consent._configure(context.$consent);
      scripts._configure(context.$scripts);
      consent._announce();
    },
  };
}

window.Next._land("scripts", createExtrasChunk);
