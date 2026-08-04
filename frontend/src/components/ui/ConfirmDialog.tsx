import React from "react";
import Modal from "./Modal";
import { Button } from "./Button";

export interface ConfirmDialogProps {
  open: boolean;
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** "danger" = red confirm button (default for destructive actions) */
  variant?: "danger" | "primary";
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * ConfirmDialog — replaces the 5 native `window.confirm()` calls in the app
 * with a proper modal that can be keyboard-dismissed, focus-trapped, and styled.
 */
export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  open,
  title = "Are you sure?",
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  variant = "danger",
  onConfirm,
  onCancel,
}) => {
  return (
    <Modal open={open} onClose={onCancel}>
      <div className="modal-header">
        <h2>{title}</h2>
      </div>
      <div className="confirm-dialog-body">
        <p>{message}</p>
      </div>
      <div className="modal-footer">
        <Button variant="ghost" onClick={onCancel}>
          {cancelLabel}
        </Button>
        <Button variant={variant} onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  );
};

export default ConfirmDialog;
