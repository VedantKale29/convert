// Bundles the demo with the pinned React from build_workspace (no extra install needed).
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const workspace = path.resolve(here, "../../../build_workspace");
const esbuild = createRequire(path.join(workspace, "package.json"))("esbuild");
await esbuild.build({
  entryPoints: [path.join(here, "main.jsx")], bundle: true, minify: true, outfile: path.join(here, "bundle.js"),
  loader: { ".js": "jsx", ".jsx": "jsx" }, nodePaths: [path.join(workspace, "node_modules")],
  define: { "process.env.NODE_ENV": '"production"' }, logLevel: "error",
});
console.log("built demo/bundle.js");
