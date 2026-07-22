import React from "react";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md";

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  busy?: boolean;
  busyLabel?: string;
  children: React.ReactNode;
}

/**
 * Design-system Button — wraps `.btn` CSS class with automatic busy-spinner
 * support. When `busy=true` the button is disabled and its label is replaced
 * with a spinner + `busyLabel` (defaults to the original children).
 */
export const Button: React.FC<ButtonProps> = ({
  variant = "primary",
  size,
  busy = false,
  busyLabel,
  children,
  disabled,
  className = "",
  ...rest
}) => {
  const sizeClass = size === "sm" ? " btn-sm" : "";
  const variantClass = variant === "primary" ? " btn-primary"
    : variant === "secondary" ? " btn-secondary"
    : variant === "ghost" ? " btn-ghost"
    : " btn-danger";

  return (
    <button
      className={`btn${variantClass}${sizeClass}${className ? ` ${className}` : ""}`}
      disabled={disabled || busy}
      {...rest}
    >
      {busy ? (
        <>
          <span className="spinner-btn" />
          {busyLabel ?? children}
        </>
      ) : (
        children
      )}
    </button>
  );
};

export default Button;
