import React from "react";
import { Icon, type IconName } from "./Icon";

export type BannerKind = "error" | "success" | "info" | "warning";

export interface BannerProps {
  kind: BannerKind;
  message: string;
  onDismiss?: () => void;
}

const ICONS: Record<BannerKind, IconName> = {
  error: "alert",
  success: "check",
  info: "info",
  warning: "alert",
};

/**
 * Banner — inline error/success/info bar.
 * Replaces the five different error-banner patterns (.ds-error / .ds-success /
 * .ai-summary-banner / form-error-as-banner etc.) with one unified look.
 */
export const Banner: React.FC<BannerProps> = ({ kind, message, onDismiss }) => {
  return (
    <div className={`banner banner--${kind}`} role={kind === "error" ? "alert" : "status"}>
      <span className="banner-icon">
        <Icon name={ICONS[kind]} size={15} />
      </span>
      <span className="banner-message">{message}</span>
      {onDismiss && (
        <button
          className="banner-close"
          onClick={onDismiss}
          aria-label="Dismiss"
        >
          <Icon name="close" size={14} />
        </button>
      )}
    </div>
  );
};

export default Banner;
