import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import { api } from "./api.js";
import { readShellSession, redirectToLogin } from "./lib/session.js";
import "./theme.css";

// UIFPE-487: the door. With a shell session, render straight away (every API
// call carries its token). Without one, ask the OPEN /health endpoint whether
// this deployment enforces auth: enforced → round-trip through the shell
// login and come back here; not enforced (local dev) or unreachable → render,
// and let the API answer for itself. The door is UX — the API's own 401 is
// the security boundary.
async function boot() {
  if (!readShellSession()) {
    try {
      const health = await api.health();
      if (health?.auth_enabled) {
        redirectToLogin();
        return;
      }
    } catch {
      // API unreachable: render — the shell shows "unreachable" honestly.
    }
  }
  createRoot(document.getElementById("root")).render(<App />);
}

boot();
