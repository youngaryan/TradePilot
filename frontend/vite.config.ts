import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxy = {
  "/api": {
    target: "http://127.0.0.1:8000",
    changeOrigin: true
  }
};

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: apiProxy
  },
  // `vite preview` serves the production bundle for the Playwright suite. It
  // needs the same API proxy as the dev server so end-to-end tests can exercise
  // authenticated, role-aware behaviour against a locally running backend.
  preview: {
    proxy: apiProxy
  }
});
