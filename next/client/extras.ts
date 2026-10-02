// The entry of next.scripts.min.js, consent and third-party scripts. The runtime
// fetches it on demand and this module hands itself over on evaluation.

import type { Extras, ExtrasHost } from "./chunks";
import { createConsent } from "./consent";
import { createScripts } from "./scripts";

/** Build the chunk's surfaces over what the runtime lends it. */
export function createExtrasChunk(host: ExtrasHost): Extras {
  const consent = createConsent({
    dispatch: host.dispatch,
    onChange: () => scripts._refresh(),
    onReveal: host.mount,
  });
  const scripts = createScripts({
    dispatch: host.dispatch,
    // A joint category names several, space-separated, and waits for each of them.
    allows: (category) =>
      category.split(" ").every((name) => consent.get()[name] === true),
    nonce: host.nonce,
  });
  // Consented markup a morph or a layer body brings lies inside the nodes it touched.
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
