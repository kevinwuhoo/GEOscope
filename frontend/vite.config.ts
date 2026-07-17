import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const preventFontSwap = {
  name: "prevent-font-swap",
  transform(code: string, id: string) {
    return id.includes("@fontsource") && id.includes(".css")
      ? code.replaceAll("font-display: swap", "font-display: optional")
      : undefined;
  },
};

export default defineConfig({
  plugins: [preventFontSwap, react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
