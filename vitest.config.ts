import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./frontend_src/__tests__/setup.ts"],
    include: ["frontend_src/__tests__/**/*.test.{ts,tsx}"],
  },
});
