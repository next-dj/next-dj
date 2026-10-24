// The entry point of next.stream.min.js, the shared stream state machine.

import { createSse } from "../sse";

window.Next._land("stream", createSse);
