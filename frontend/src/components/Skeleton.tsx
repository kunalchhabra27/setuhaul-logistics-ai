import type { LucideIcon } from "lucide-react";

interface SkeletonHeaderProps {
  color: string;
  colorSoft: string;
  icon: LucideIcon;
  label: string;
}

/** Small branded strip shared by both skeleton shapes below: a bobbing
 * portal icon plus the same dashed "road" bar TruckTransition uses for the
 * full-page portal-switch animation (index.css's .road-strip + animate-drift),
 * scaled down into an inline loading indicator so the two moments share one
 * visual language instead of introducing a second, generic spinner style. */
function SkeletonHeader({ color, colorSoft, icon: Icon, label }: SkeletonHeaderProps) {
  return (
    <div className="flex items-center gap-3">
      <div
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full animate-bob"
        style={{ background: colorSoft, color }}
      >
        <Icon className="h-4 w-4" />
      </div>
      <div className="flex-1">
        <p className="text-xs font-bold text-ink-soft">{label}</p>
        <div
          className="road-strip mt-1.5 h-[3px] w-full max-w-[220px] animate-drift rounded-full opacity-70"
          style={{ backgroundColor: color, backgroundSize: "40px 100%" }}
        />
      </div>
    </div>
  );
}

interface SkeletonRowsProps {
  color: string;
  colorSoft: string;
  icon: LucideIcon;
  label: string;
  rows?: number;
  columns?: number;
}

/** Table-row-shaped loading placeholder for the TMS/WMS/Check-in shipment
 * tables -- replaces the previous behavior of rendering the real table with
 * zero rows (which showed the "No shipments yet" empty state for a moment
 * on every load, before the first fetch resolved). */
export function SkeletonRows({ color, colorSoft, icon, label, rows = 5, columns = 6 }: SkeletonRowsProps) {
  return (
    <div className="space-y-4" role="status" aria-label={label}>
      <SkeletonHeader color={color} colorSoft={colorSoft} icon={icon} label={label} />
      <div className="overflow-hidden rounded-2xl border border-line">
        <table className="w-full border-collapse text-sm">
          <tbody>
            {Array.from({ length: rows }).map((_, rowIndex) => (
              <tr key={rowIndex} className="border-t border-line first:border-t-0">
                {Array.from({ length: columns }).map((__, colIndex) => (
                  <td key={colIndex} className="px-3 py-3.5">
                    <div
                      className="h-3 animate-pulse rounded-full bg-cloud"
                      style={{
                        width: colIndex === 0 ? "70%" : `${55 + ((rowIndex + colIndex) % 3) * 12}%`,
                        animationDelay: `${(rowIndex * columns + colIndex) * 40}ms`,
                      }}
                    />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

interface SkeletonCardProps {
  color: string;
  colorSoft: string;
  icon: LucideIcon;
  label: string;
  lines?: number;
}

/** Block-shaped loading placeholder for single-record views (driver
 * snapshot panels, facility-assignment loading, etc.) -- same branded
 * header as SkeletonRows, with pulsing content blocks instead of a table. */
export function SkeletonCard({ color, colorSoft, icon, label, lines = 3 }: SkeletonCardProps) {
  return (
    <div className="space-y-4 rounded-2xl border border-line bg-white p-5" role="status" aria-label={label}>
      <SkeletonHeader color={color} colorSoft={colorSoft} icon={icon} label={label} />
      <div className="space-y-2.5">
        {Array.from({ length: lines }).map((_, index) => (
          <div
            key={index}
            className="h-3 animate-pulse rounded-full bg-cloud"
            style={{ width: `${85 - index * 15}%`, animationDelay: `${index * 60}ms` }}
          />
        ))}
      </div>
    </div>
  );
}
