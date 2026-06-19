import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

// Second build target: the in-context profile injector. Emitted alongside the
// panel bundle into custom_components/atm/frontend/ (emptyOutDir: false so it does
// not wipe atm-panel.js). ES-module format (loaded via the frontend extra-module
// mechanism) with code-splitting left ON, so the heavy modal (React +
// ProfileEditor) becomes a separate chunk that the tiny always-on injector
// lazy-imports only on first use.
export default defineConfig({
  plugins: [react()],
  base: "/local/atm/",
  build: {
    outDir: "custom_components/atm/frontend",
    emptyOutDir: false,
    rollupOptions: {
      input: resolve(__dirname, "frontend_src/inject/index.ts"),
      output: {
        format: "es",
        entryFileNames: "atm-inject.js",
        // Stable chunk name (no content hash): the panel static path is served
        // with cache_headers=False, so freshness is handled there, and a stable
        // name avoids stale hashed chunks accumulating in frontend/ each build.
        chunkFileNames: "atm-inject-quickadd.js",
        assetFileNames: "[name][extname]",
      },
    },
    target: "es2020",
    minify: true,
  },
});
