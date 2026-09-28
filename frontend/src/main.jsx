import React from "react";
import ReactDOM from "react-dom/client";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import "xterm/css/xterm.css";
import "./styles.css";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";

// Apply the saved theme before first paint (default: light) to avoid a flash.
// Mode can be "light", "dark", or "auto" (follows local time).
try {
  const mode = window.localStorage.getItem("projectDeck.theme") || "auto";
  let effective = mode;
  if (mode === "auto") {
    const hour = new Date().getHours();
    effective = hour >= 19 || hour < 7 ? "dark" : "light";
  }
  document.documentElement.dataset.theme = effective === "dark" ? "dark" : "light";
} catch (error) {
  document.documentElement.dataset.theme = "light";
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </React.StrictMode>
);

