import { User, Truck, MapPin, AlertTriangle, ShieldCheck, CheckCircle2 } from "lucide-react";
import type { DriverExceptionStatus, DriverSnapshot } from "../../types/driverChat";

function formatTime(iso?: string | null) {
  if (!iso) return "N/A";
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

function ExceptionBadge({ status, color }: { status?: DriverExceptionStatus | null; color: string }) {
  switch (status) {
    case "OPEN":
    case "NEEDS_INFORMATION":
    case "SLOT_OPTIONS_SHARED":
    case "WAITING_CONFIRMATION":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-bold" style={{ background: `${color}1A`, color }}>
          <AlertTriangle className="h-4 w-4" /> Delay exception reported
        </span>
      );
    case "RESOLVED":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-bold text-emerald-700">
          <CheckCircle2 className="h-4 w-4" /> Dock time confirmed
        </span>
      );
    case "ESCALATED":
      return (
        <span className="inline-flex items-center gap-1.5 rounded-xl border border-rose-200 bg-rose-50 px-3 py-1.5 text-xs font-bold text-rose-700">
          <AlertTriangle className="h-4 w-4" /> Connecting to human dispatcher
        </span>
      );
    default:
      return (
        <span className="inline-flex items-center gap-1.5 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-xs font-bold text-emerald-700">
          <ShieldCheck className="h-4 w-4" /> Active trip on time
        </span>
      );
  }
}

export default function ContextBar({
  snapshot,
  color,
  onQuickUpdateEta,
}: {
  snapshot: DriverSnapshot;
  color: string;
  onQuickUpdateEta: (minutes: number) => void;
}) {
  const { driver, vehicle, shipment, facility, exception } = snapshot;

  return (
    <div className="rounded-2xl border border-line bg-white p-4 sm:p-5">
      <div className="grid grid-cols-1 gap-4 divide-y divide-line sm:grid-cols-2 sm:gap-6 sm:divide-y-0 sm:divide-x lg:grid-cols-4">
        <div className="flex items-center gap-3 pt-2 sm:pt-0">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl" style={{ background: `${color}1A`, color }}>
            <User className="h-6 w-6" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-extrabold text-ink sm:text-lg">{driver.driver_name || "Driver"}</h3>
              <span className="rounded-md bg-cloud px-2 py-0.5 font-mono text-xs font-bold" style={{ color }}>
                {driver.driver_id.slice(0, 8)}
              </span>
            </div>
            <p className="text-xs font-semibold text-ink-soft">{driver.home_base_city || "Fleet partner"}</p>
            <p className="mt-0.5 text-xs text-mist">{driver.phone}</p>
          </div>
        </div>

        <div className="flex items-center gap-3 pt-4 sm:pt-0 sm:pl-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-tms-soft text-tms">
            <Truck className="h-6 w-6" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-base font-black text-ink">{vehicle?.registration_number || "Unassigned"}</span>
              <span className="rounded-md bg-tms-soft px-2 py-0.5 text-[11px] font-bold text-tms">{vehicle?.vehicle_type_code || "—"}</span>
            </div>
            <p className="mt-0.5 text-xs font-medium text-ink-soft">
              Cargo: <span className="font-bold text-ink">{shipment?.product_category || "N/A"}</span>
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 pt-4 sm:pt-0 lg:pl-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-checkin-soft text-checkin">
            <MapPin className="h-6 w-6" />
          </div>
          <div className="min-w-0 flex-1">
            <span className="font-mono text-xs font-bold" style={{ color }}>
              {shipment?.shipment_id.slice(0, 8)}
            </span>
            <h4 className="mt-0.5 truncate text-sm font-extrabold text-ink sm:text-base">{facility?.facility_name || "Destination facility"}</h4>
            <p className="text-xs text-mist">Gate hours: {facility?.open_time || "06:00"} - {facility?.close_time || "23:00"}</p>
          </div>
        </div>

        <div className="flex flex-col justify-between gap-2 pt-4 sm:pt-0 lg:pl-4">
          <div>
            <div className="mb-1 flex items-center justify-between">
              <span className="text-xs font-bold text-ink-soft">Planned ETA</span>
              <span className="rounded-lg px-2.5 py-1 font-mono text-sm font-black" style={{ background: `${color}1A`, color }}>
                {formatTime(exception?.declared_eta_ts || shipment?.latest_eta_ts || shipment?.original_eta_ts)}
              </span>
            </div>
            <div className="flex items-center justify-between text-xs text-mist">
              <span>Original: {formatTime(shipment?.original_eta_ts)}</span>
              <span>Unload ~{shipment?.expected_unload_min || 45}m</span>
            </div>
          </div>

          <div className="flex items-center justify-between gap-2">
            <ExceptionBadge status={exception?.exception_status} color={color} />
            <div className="flex items-center gap-1">
              <button
                onClick={() => onQuickUpdateEta(45)}
                className="rounded-xl border border-line bg-cloud px-2.5 py-1.5 text-xs font-bold text-ink-soft transition hover:bg-line"
              >
                +45m
              </button>
              <button
                onClick={() => onQuickUpdateEta(90)}
                className="rounded-xl px-2.5 py-1.5 text-xs font-bold transition hover:opacity-80"
                style={{ background: `${color}1A`, color }}
              >
                +90m
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
