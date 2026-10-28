import { Sparkles } from "lucide-react";

interface EmptyStateVisualProps {
  className?: string;
  /** Square container edge in px; default 160. */
  size?: number;
}

export function EmptyStateVisual({
  className,
  size = 160,
}: EmptyStateVisualProps) {
  return (
    <span
      aria-hidden
      className={className}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        width: size,
        height: size,
        color: "var(--fn-text-quaternary, #bfbfbf)",
        flexShrink: 0,
      }}
    >
      <Sparkles size={size >= 160 ? 72 : 56} strokeWidth={1.2} />
    </span>
  );
}
