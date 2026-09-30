// The entry of next.dev.min.js, handing the dev channel to the runtime as it evaluates.

import { createDiagnostics } from "./diagnostics";

window.Next._diagnostics(createDiagnostics());
