interface EmptyStateProps {
  message: string;
  /** Table empty states render inside a <tr><td colSpan=...>; standalone
   * ones (dropdowns, cards) render as a plain block. Same italic/muted
   * copy style either way -- matches the "No shipments yet" text already
   * used throughout the app, just no longer copy-pasted per call site. */
  as?: "row" | "block";
  colSpan?: number;
}

export function EmptyState({ message, as = "block", colSpan }: EmptyStateProps) {
  if (as === "row") {
    return (
      <tr>
        <td colSpan={colSpan} className="py-6 text-center text-sm text-ink-soft">
          {message}
        </td>
      </tr>
    );
  }
  return <p className="py-6 text-center text-sm italic text-mist">{message}</p>;
}
