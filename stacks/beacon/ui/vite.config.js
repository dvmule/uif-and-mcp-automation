import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Dev server proxies /api to the FastAPI service so the UI code always calls
// same-origin /api/* — identical to the deployed shape (CloudFront/API
// Gateway route), which keeps the eventual RELAY-shell merge a lift-and-shift.
// Point the dev server at a deployed environment to check the UI against real
// data:  BEACON_API_PROXY=https://<dist>.cloudfront.net npm run dev
// A deployed origin routes /api/* itself (the CloudFront function strips the
// prefix), so the rewrite applies to the local FastAPI target only.
const target = process.env.BEACON_API_PROXY || "http://127.0.0.1:8802";
const isLocal = !process.env.BEACON_API_PROXY;

export default defineConfig({
  // Relative asset base (transform-UI pattern, UIFPE-99): the app deploys
  // under a PREFIX of the shared RELAY CloudFront distribution
  // (/m/beacon/), and the same bundle serves the standalone sandbox
  // distribution at the root. Hash routing makes prefixes route-free.
  base: "./",
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target,
        changeOrigin: true,
        ...(isLocal ? { rewrite: (path) => path.replace(/^\/api/, "") } : {}),
      },
    },
  },
});
