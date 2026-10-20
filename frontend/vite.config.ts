import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    port: 5173,
    // The API runs separately in development (port 8010, so it never clashes with other local
    // projects on 8000); the browser only ever talks to one origin.
    proxy: { "/api": process.env.FNB_API_URL ?? "http://127.0.0.1:8010" },
  },
  build: { sourcemap: false },
});
