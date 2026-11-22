import { once } from "../protocol";
import { createReload } from "../reload";

once("reload", () => createReload(window.Next));
