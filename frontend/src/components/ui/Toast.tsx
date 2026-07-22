import React, {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  useEffect,
} from "react";
import { createPortal } from "react-dom";

// ── Types ─────────────────────────────────────────────────────────────────────

export type ToastKind = "success" | "error" | "info";

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
  /** Auto-dismiss after ms (default 4 000). 0 = persistent until dismissed. */
  duration?: number;
  /** Animated-out but still mounted (so exit animation plays) */
  exiting: boolean;
}

interface ToastContextValue {
  toast: (message: string, kind?: ToastKind, duration?: number) => void;
  success: (message: string, duration?: number) => void;
  error: (message: string, duration?: number) => void;
  info: (message: string, duration?: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

// ── Provider ──────────────────────────────────────────────────────────────────

let nextId = 0;
const DEFAULT_DURATION = 4000;

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const timers = useRef<Record<number, ReturnType<typeof setTimeout>>>({});

  const dismiss = useCallback((id: number) => {
    // Start exit animation
    setToasts((prev) =>
      prev.map((t) => (t.id === id ? { ...t, exiting: true } : t))
    );
    // Remove from DOM after animation completes (~300ms)
    timers.current[id] = setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
      delete timers.current[id];
    }, 320);
  }, []);

  const toast = useCallback(
    (message: string, kind: ToastKind = "info", duration = DEFAULT_DURATION) => {
      const id = ++nextId;
      setToasts((prev) => [
        ...prev,
        { id, kind, message, duration, exiting: false },
      ]);
      if (duration > 0) {
        timers.current[id] = setTimeout(() => dismiss(id), duration);
      }
    },
    [dismiss]
  );

  const success = useCallback(
    (message: string, duration?: number) => toast(message, "success", duration),
    [toast]
  );
  const error = useCallback(
    (message: string, duration?: number) => toast(message, "error", duration),
    [toast]
  );
  const info = useCallback(
    (message: string, duration?: number) => toast(message, "info", duration),
    [toast]
  );

  // Cleanup on unmount
  useEffect(() => {
    const t = timers.current;
    return () => Object.values(t).forEach(clearTimeout);
  }, []);

  return (
    <ToastContext.Provider value={{ toast, success, error, info }}>
      {children}
      {createPortal(
        <div className="toast-container" aria-live="polite" aria-atomic="false">
          {toasts.map((t) => (
            <ToastBubble key={t.id} toast={t} onDismiss={dismiss} />
          ))}
        </div>,
        document.body
      )}
    </ToastContext.Provider>
  );
};

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx)
    throw new Error("useToast must be used inside <ToastProvider>");
  return ctx;
}

// ── Toast bubble ──────────────────────────────────────────────────────────────

const ICONS: Record<ToastKind, string> = {
  success: "✓",
  error: "✕",
  info: "ℹ",
};

const ToastBubble: React.FC<{
  toast: ToastItem;
  onDismiss: (id: number) => void;
}> = ({ toast, onDismiss }) => {
  return (
    <div
      className={`toast toast--${toast.kind}${toast.exiting ? " toast--exit" : ""}`}
      role="alert"
    >
      <span className="toast-icon">{ICONS[toast.kind]}</span>
      <span className="toast-message">{toast.message}</span>
      <button
        className="toast-close"
        onClick={() => onDismiss(toast.id)}
        aria-label="Dismiss notification"
      >
        ✕
      </button>
    </div>
  );
};

export default ToastProvider;
