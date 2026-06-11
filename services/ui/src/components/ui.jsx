/**
 * ui.jsx — Shared low-level UI primitives
 */
import clsx from "clsx";

// ─── Spinner ─────────────────────────────────────────────────────────────────
export function Spinner({ size = "sm", className }) {
  const sz = size === "sm" ? "w-4 h-4" : size === "md" ? "w-6 h-6" : "w-8 h-8";
  return (
    <svg
      className={clsx("animate-spin text-accent", sz, className)}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
    </svg>
  );
}

// ─── Typing dots ──────────────────────────────────────────────────────────────
export function TypingDots() {
  return (
    <span className="inline-flex items-center gap-1 py-1">
      {[0, 0.2, 0.4].map((d, i) => (
        <span
          key={i}
          className="w-1.5 h-1.5 rounded-full bg-text-secondary animate-pulse-dot"
          style={{ animationDelay: `${d}s` }}
        />
      ))}
    </span>
  );
}

// ─── Badge ────────────────────────────────────────────────────────────────────
export function Badge({ children, variant = "default", className }) {
  const variants = {
    default: "bg-surface-3 text-text-secondary",
    accent:  "bg-accent/10 text-accent border border-accent/20",
    teal:    "bg-teal/10 text-teal border border-teal/20",
    amber:   "bg-amber/10 text-amber border border-amber/20",
    red:     "bg-red/10 text-red border border-red/20",
    success: "bg-teal/10 text-teal border border-teal/20",
  };
  return (
    <span className={clsx("badge", variants[variant], className)}>
      {children}
    </span>
  );
}

// ─── Divider ──────────────────────────────────────────────────────────────────
export function Divider({ className }) {
  return <hr className={clsx("border-surface-4", className)} />;
}

// ─── Avatar / Initial ─────────────────────────────────────────────────────────
export function Avatar({ name, size = "sm" }) {
  const initials = name
    ? name.split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase()
    : "?";
  const sz = size === "sm" ? "w-7 h-7 text-xs" : "w-9 h-9 text-sm";
  return (
    <span className={clsx(
      "rounded-full bg-accent/20 text-accent font-semibold flex items-center justify-center flex-shrink-0 font-mono",
      sz,
    )}>
      {initials}
    </span>
  );
}

// ─── Tooltip (lightweight) ───────────────────────────────────────────────────
export function Tooltip({ label, children }) {
  return (
    <span className="relative group">
      {children}
      <span className="pointer-events-none absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 px-2 py-1 rounded text-xs bg-surface-0 text-text-primary border border-surface-4 whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity z-50">
        {label}
      </span>
    </span>
  );
}
