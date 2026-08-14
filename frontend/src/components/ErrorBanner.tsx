import type { ReactNode } from "react";

interface ErrorBannerProps {
  children: ReactNode;
  className?: string;
}

/** The rose-colored error banner used across the app (TMS/WMS/Check-in
 * panels, auth forms, facility setup) -- extracted so the same markup isn't
 * hand-copied at every call site. Visually identical to the banner it
 * replaces (`border-rose-200 bg-rose-50 ... text-rose-700`, no icon). */
export function ErrorBanner({ children, className = "" }: ErrorBannerProps) {
  return (
    <div role="alert" className={`rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 ${className}`}>
      {children}
    </div>
  );
}
