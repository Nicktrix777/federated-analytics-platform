import ReactDOM from "react-dom/client";
import App from "./App";
import { ToastProvider } from "./components/ui/Toast";

// NOTE: React.StrictMode is intentionally NOT used here. Its dev-only double
// invocation of effects desyncs react-draggable's (the engine behind
// react-grid-layout) mousedown handler binding, so its preventDefault never
// fires and the browser starts a NATIVE HTML5 drag instead — the widget won't
// move and you get a red "no-drop" cursor. This is a known RGL + StrictMode
// incompatibility; dropping StrictMode is the standard workaround.
ReactDOM.createRoot(document.getElementById("root")!).render(
  <ToastProvider>
    <App />
  </ToastProvider>
);
