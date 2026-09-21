// SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
export default defineConfig({
  plugins: [react(), tailwindcss()],
  css: { postcss: { plugins: [] } },
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: process.env.ONECAT_API_TARGET || "http://127.0.0.1:8888",
        changeOrigin: false,
      },
      "/v1": {
        target: process.env.ONECAT_API_TARGET || "http://127.0.0.1:8888",
        changeOrigin: false,
      },
    },
  },
  build: { outDir: "dist" },
});
