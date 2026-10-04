// The entry point of next.dev.min.js, which registers the dev diagnostics.

import { createDiagnostics } from "./diagnostics";

// The chunk evaluates after _init seeded the context, so the payload can be checked.
window.Next._land("dev", createDiagnostics(window.Next.context));
