import React, { useEffect, useRef, useCallback, useState } from "react";
import { createPortal } from "react-dom";

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  /** Extra class on .modal-box, e.g. "modal-lg" */
  boxClass?: string;
  children: React.ReactNode;
  /** When true, clicking the overlay or pressing Esc does not close the modal */
  persistent?: boolean;
}

/**
 * Modal — wraps the existing `.modal-overlay` / `.modal-box` CSS with:
 *  - Esc key handling
 *  - Focus trap (Tab/Shift+Tab cycle inside the modal)
 *  - Body scroll-lock while open
 *  - Rendered into a portal so z-index stacking is clean
 */
export const Modal: React.FC<ModalProps> = ({
  open,
  onClose,
  boxClass = "",
  persistent = false,
  children,
}) => {
  const boxRef = useRef<HTMLDivElement>(null);

  const [isMounted, setIsMounted] = useState(false);
  const [isExiting, setIsExiting] = useState(false);

  useEffect(() => {
    if (open) {
      setIsMounted(true);
      setIsExiting(false);
    } else if (isMounted) {
      setIsExiting(true);
      const timer = setTimeout(() => {
        setIsMounted(false);
        setIsExiting(false);
      }, 250);
      return () => clearTimeout(timer);
    }
  }, [open, isMounted]);

  // -- Scroll lock --
  useEffect(() => {
    if (!isMounted) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [isMounted]);

  // -- Esc key --
  useEffect(() => {
    if (!open || persistent) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose, persistent]);

  // -- Focus trap --
  const trapFocus = useCallback((e: React.KeyboardEvent) => {
    if (e.key !== "Tab" || !boxRef.current) return;
    const focusable = Array.from(
      boxRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      )
    ).filter((el) => !el.hasAttribute("disabled"));
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey) {
      if (document.activeElement === first) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }, []);

  // -- Auto-focus first focusable element when opened --
  useEffect(() => {
    if (!open || !boxRef.current) return;
    const first = boxRef.current.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    // Small defer so animation doesn't fight with focus
    const id = setTimeout(() => first?.focus(), 50);
    return () => clearTimeout(id);
  }, [open]);

  if (!isMounted) return null;

  return createPortal(
    <div
      className={`modal-overlay${isExiting ? " modal-exiting" : ""}`}
      onClick={persistent ? undefined : onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        ref={boxRef}
        className={`modal-box${boxClass ? ` ${boxClass}` : ""}${isExiting ? " modal-exiting" : ""}`}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={trapFocus}
      >
        {children}
      </div>
    </div>,
    document.body
  );
};

export default Modal;
