import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// The wizard reads REGISTERED artifacts across ALL domains directly (intake
// mappings discovered via glob) so what you see is always the real current
// state — no copy that can drift. fs.allow opens the domain root to the dev
// server; the build inlines them.
//
// THIS IS A CI DEPENDENCY, not just a build one. The tests read those same
// artifacts, so a domains/ change is a change to this app's test inputs. What
// makes a domain change run echo's CI is stacks/echo/ci-watch, which declares
// these paths; stacks-ci.yml matrixes per stack and would otherwise never
// select echo for a domains-only diff (UIFPE-728, UIFPE-738). Add a glob here
// and add it there too, or the tests it feeds cannot fail in CI.
export default defineConfig({
  // Relative asset base: the app deploys under a PREFIX of the shared RELAY
  // CloudFront distribution (/m/transform/ — UIFPE-99); hash routing plus a
  // relative base means the same bundle works locally and under any prefix.
  base: "./",
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src"),
             "@schemas": path.resolve(__dirname, "../../../domains/product/src/schemas") },
  },
  server: {
    port: 5183,
    fs: { allow: [path.resolve(__dirname, "../../..")] },
  },
});
