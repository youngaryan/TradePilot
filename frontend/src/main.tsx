import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router";

import App from "./App";
import { ApolloApp } from "./features/ApolloApp";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        {/* Apollo shell (new design) is the primary app. */}
        <Route path="/" element={<ApolloApp />} />
        <Route path="/apollo" element={<ApolloApp />} />
        {/* Classic QuantOps console (Account, Pricing, Admin) remains available. */}
        <Route path="*" element={<App />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);
