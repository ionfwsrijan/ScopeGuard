import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const node = process.execPath;
for (const args of [
  [resolve(root, "node_modules/typescript/bin/tsc"), "-b"],
  [resolve(root, "node_modules/vite/bin/vite.js"), "build"],
]) {
  const result = spawnSync(node, args, { cwd: root, stdio: "inherit" });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status ?? 1);
}
