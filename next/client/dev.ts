// The entry of next.dev.min.js, handing the dev channel to the runtime as it evaluates.

import { createDiagnostics, warnCsrf } from "./diagnostics";

// The chunk lands after _init seeded the context, so the payload is there to check.
warnCsrf(window.Next.context);
window.Next._land("dev", createDiagnostics());
