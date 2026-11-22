import { createMore } from "../more";
import { once } from "../protocol";

once("more", () => createMore(window.Next));
