import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

export default defineConfig({
  plugins: [react()],
  base: "/local/atm/",
  build: {
    outDir: "custom_components/atm/frontend",
    emptyOutDir: false,
    rollupOptions: {
      input: resolve(__dirname, "frontend_src/index.tsx"),
      output: {
        format: "iife",
        entryFileNames: "atm-panel.js",
        assetFileNames: "[name][extname]",
        inlineDynamicImports: true,
      },
    },
    target: "es2020",
    minify: true,
  },
});
