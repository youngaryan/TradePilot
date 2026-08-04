import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { AppRoot } from "./app/AppRoot";
import { LEGACY_HASH_ROUTES } from "./shell/navigation";
import "./styles.css";

/**
 * Translate a legacy `#/app/<view>` deep link into its canonical path *before*
 * the router mounts, so the address the router boots with is already correct and
 * no redirect can race the translation.
 */
function resolveLegacyHashRoute() {
  const hash = window.location.hash.replace(/^#\/?/, "");
  if (!hash.startsWith("app/")) return;
  const view = hash.slice("app/".length).split(/[/?]/)[0];
  const target = LEGACY_HASH_ROUTES[view];
  if (!target) return;
  window.history.replaceState(null, "", `${target}${window.location.search}`);
}

resolveLegacyHashRoute();

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <AppRoot />
    </BrowserRouter>
  </React.StrictMode>,
);
