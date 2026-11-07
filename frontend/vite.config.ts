import path from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const root = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig(({ command }) => ({
  base: command === "build" ? "/static/app/" : "/",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(root, "src") } },
  server: { proxy: { "/api": "http://127.0.0.1:8787", "/demo-video": "http://127.0.0.1:8787" } },
  build: {
    outDir: path.resolve(root, "../src/scopeguard/api/static/app"),
    emptyOutDir: true,
    assetsDir: "assets",
  },
}));
