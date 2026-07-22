import React from "react";

export type BannerKind = "error" | "success" | "info" | "warning";

export interface BannerProps {
  kind: BannerKind;
  message: string;
  onDismiss?: () => void;
}

const ICONS: Record<BannerKind, string> = {
  error: "⚠️",
  success: "✓",
  info: "ℹ",
  warning: "⚠",
};

/**
 * Banner — inline error/success/info bar.
 * Replaces the five different error-banner patterns (.ds-error / .ds-success /
 * .ai-summary-banner / form-error-as-banner etc.) with one unified look.
 */
export const Banner: React.FC<BannerProps> = ({ kind, message, onDismiss }) => {
  return (
    <div className={`banner banner--${kind}`} role={kind === "error" ? "alert" : "status"}>
      <span className="banner-icon">{ICONS[kind]}</span>
      <span className="banner-message">{message}</span>
      {onDismiss && (
        <button
          className="banner-close"
          onClick={onDismiss}
          aria-label="Dismiss"
        >
          ✕
        </button>
      )}
    </div>
  );
};

export default Banner;
