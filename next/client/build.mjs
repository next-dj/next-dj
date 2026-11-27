// Compile the runtime and its lazy chunks into next/static/next with esbuild.
//
// protocol.gen.ts is written first from the Python constants of the wire protocol, by
// the interpreter PYTHON names, so the client never spells a server constant by hand.
//
// Every bundle carries the same build id, a digest of the sources, so the runtime can
// refuse a chunk a cache kept from another release. Statements labelled TEST are
// harness seams, dropped here and kept by the unit build.

import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { build } from "esbuild";

const CLIENT = "next/client";

const ENTRIES = {
  "next.min": "next/client/next.ts",
  "next.scripts.min": "next/client/entries/scripts.ts",
  "next.stream.min": "next/client/entries/stream.ts",
  "next.ws.min": "next/client/entries/ws.ts",
  "next.csrf.min": "next/client/entries/csrf.ts",
  "next.poll.min": "next/client/entries/poll.ts",
  "next.dev.min": "next/client/entries/dev.ts",
  "next.forms.min": "next/client/entries/forms.ts",
  "next.layers.min": "next/client/entries/layers.ts",
  "next.boundaries.min": "next/client/entries/boundaries.ts",
  "next.reload.min": "next/client/entries/reload.ts",
  "next.more.min": "next/client/entries/more.ts",
  "next.router.min": "next/client/entries/router.ts",
  "next.intent.min": "next/client/entries/intent.ts",
};

// Sorted file names and their bytes, so the id is the same on every machine.
function buildId() {
  const digest = createHash("sha256");
  for (const dir of [CLIENT, join(CLIENT, "entries")]) {
    const names = readdirSync(dir)
      .filter((name) => name.endsWith(".ts") && !name.endsWith(".test.ts"))
      .sort();
    for (const name of names) digest.update(name).update(readFileSync(join(dir, name)));
  }
  return digest.digest("hex").slice(0, 6);
}

const generated = spawnSync(
  process.env.PYTHON ?? "python3",
  ["next/static/protocol.py"],
  {
    stdio: "inherit",
  },
);
if (generated.status !== 0) throw new Error("next/static/protocol.py failed");

await build({
  entryPoints: ENTRIES,
  bundle: true,
  minify: true,
  target: "es2022",
  sourcemap: "external",
  outdir: "next/static/next",
  dropLabels: ["TEST"],
  define: { __BUILD__: JSON.stringify(buildId()) },
});
