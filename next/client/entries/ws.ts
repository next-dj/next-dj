// The optional WebSocket transport, fetched only when the server enables it.

import { createWebSocketTransport } from "../ws";

window.Next._land("ws", createWebSocketTransport);
