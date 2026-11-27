// The entry point of next.layers.min.js, the modal layers, fetched once a mount finds a
// layer link or an envelope opens a layer.

import { createLayers } from "../layers";

window.Next._extend("layers", () => createLayers, __BUILD__);
