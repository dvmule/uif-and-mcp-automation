import React from "react";
import ReactDOM from "react-dom/client";
import { createHashRouter, RouterProvider, Navigate } from "react-router-dom";
import "./index.css";
// Additive token layer — semantic ramps, a monospace token and a focus ring
// index.css has no vocabulary for yet. Imported AFTER it so the cascade lands.
import "./styles/tokens.css";
import { Shell } from "./App";
import { TransformHome } from "./pages/transform/TransformHome";
import { Wizard } from "./pages/transform/wizard/Wizard";
import { SourcesPage } from "./pages/transform/SourcesPage";
import { AttributesPage } from "./pages/transform/AttributesPage";
import { MySubmissionsPage } from "./pages/transform/MySubmissionsPage";

// Hash routing so the app works from any static path on the shared CloudFront
// distribution (ADR-0006 D5) without origin rewrite rules.
const router = createHashRouter([
  {
    element: <Shell />,
    children: [
      { path: "/", element: <TransformHome /> },
      { path: "/transform", element: <TransformHome /> },
      { path: "/transform/new", element: <Wizard /> },
      // The schema-first setup flow was folded into the ONE wizard
      // (UIFPE-219): creating a table is an entry point, not a second flow.
      // Old bookmarks land in the same place they used to go.
      { path: "/transform/setup",
        element: <Navigate to="/transform/new?new=1" replace /> },
      // Domain + entity in the path: every surface is scoped to one entity, and
      // the UI is deliberately domain-agnostic (repo.ts discovers all domains).
      { path: "/transform/sources/:domain/:table", element: <SourcesPage /> },
      { path: "/transform/attributes/:domain/:table", element: <AttributesPage /> },
      // Where a submitted change stands (UIFPE-218) — the author never needs
      // GitHub to know; approval itself still happens in the PR.
      { path: "/transform/submissions", element: <MySubmissionsPage /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
