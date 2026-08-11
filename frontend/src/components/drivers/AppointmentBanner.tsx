import { CheckCircle2, Clock } from "lucide-react";
import type { DriverAppointmentSummary } from "../../types/driverChat";

function formatTime(iso?: string | null) {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return null;
  }
}

// Surfaces a confirmed dock booking prominently -- previously
// snapshot.appointment carried this data but nothing in the driver portal
// ever rendered it, so a slot booked (by the driver or by WMS staff on
// their behalf) silently never showed up anywhere on this screen.
export default function AppointmentBanner({
  appointment,
  color,
}: {
  appointment?: DriverAppointmentSummary | null;
  color: string;
}) {
  if (!appointment || appointment.appointment_status !== "CONFIRMED") return null;

  const start = formatTime(appointment.slot_start_ts);
  const end = formatTime(appointment.slot_end_ts);

  return (
    <div className="flex items-center gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3.5 sm:px-5">
      <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-emerald-100 text-emerald-700">
        <CheckCircle2 className="h-6 w-6" />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-extrabold text-emerald-800">
          Dock appointment confirmed{appointment.dock_code ? ` -- ${appointment.dock_code}` : ""}
        </p>
        <p className="mt-0.5 flex items-center gap-1.5 text-xs font-semibold text-emerald-700">
          {start && end ? (
            <>
              <Clock className="h-3.5 w-3.5" /> {start} - {end}
            </>
          ) : (
            "Time window pending confirmation"
          )}
        </p>
      </div>
      <span
        className="shrink-0 rounded-full px-3 py-1 text-[11px] font-black uppercase"
        style={{ background: `${color}1A`, color }}
      >
        Confirmed
      </span>
    </div>
  );
}
