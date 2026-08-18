import { ArrowRightLeft, Clock, Lock, CheckCircle2, Info } from "lucide-react";
import type { DriverDockSummary, DriverFacilitySummary, DriverSlotOption } from "../../types/driverChat";

// The board now spans several days of backfilled capacity, not just
// "today" (see the backend's ensure_future_slots) -- without a date label,
// a plain time like "09:00 AM - 10:00 AM" repeated once per upcoming day
// looked like duplicated or out-of-range data.
function formatDayLabel(iso: string) {
  const date = new Date(iso);
  const today = new Date();
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  if (date.toDateString() === today.toDateString()) return "Today";
  if (date.toDateString() === tomorrow.toDateString()) return "Tomorrow";
  return date.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
}

function dayKey(iso: string) {
  return new Date(iso).toDateString();
}

export default function DockSlotBoard({
  color,
  facility,
  docks,
  slotOptions,
  onHoldSlot,
  onConfirmSlot,
  onRequestSlotChange,
  disabled,
}: {
  color: string;
  facility?: DriverFacilitySummary | null;
  docks: DriverDockSummary[];
  slotOptions: DriverSlotOption[];
  onHoldSlot: (slotId: string) => Promise<void>;
  onConfirmSlot: (slotId: string) => Promise<void>;
  onRequestSlotChange: (slotId: string) => Promise<void>;
  // True while a hold/confirm/request-change/check-in action is already in
  // flight elsewhere in the portal -- disables these buttons so a
  // double-tap can't fire a second overlapping request (see DriversPortal's
  // dockActionLockRef).
  disabled?: boolean;
}) {
  // Once a slot is already confirmed, the hold -> confirm flow no longer
  // applies to other slots -- switching means asking WMS to move the
  // existing appointment, not booking a second one from scratch.
  const hasConfirmedBooking = slotOptions.some((opt) => opt.is_booked_by_me);
  return (
    <div className="space-y-4 rounded-2xl border border-line bg-white p-4 sm:p-5">
      <div className="flex flex-col justify-between gap-3 border-b border-line pb-3 sm:flex-row sm:items-center">
        <div>
          <h2 className="flex items-center gap-2 text-base font-extrabold text-ink sm:text-lg">
            Dock unloading slots
            <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-0.5 text-xs font-bold text-emerald-700">Live doors</span>
          </h2>
          <p className="mt-0.5 text-xs text-mist">Select an open time slot to reserve your unloading bay</p>
        </div>
      </div>

      <div className="flex items-center gap-2.5 rounded-xl border border-line bg-cloud p-3">
        <Info className="h-4 w-4 shrink-0" style={{ color }} />
        <span className="text-xs text-ink-soft">
          Open hours: <strong className="text-ink">{facility?.open_time || "06:00"} - {facility?.close_time || "23:00"}</strong>
        </span>
      </div>

      <div className="grid max-h-[480px] grid-cols-1 gap-3 overflow-y-auto p-1 sm:grid-cols-2">
        {docks.map((dock) => {
          const dockOptions = slotOptions.filter((s) => s.dock_id === dock.dock_id);
          return (
            <div key={dock.dock_id} className="space-y-2.5 rounded-xl border border-line bg-cloud p-3">
              <div className="flex items-center justify-between border-b border-line pb-2">
                <div>
                  <h3 className="text-sm font-extrabold text-ink">{dock.dock_code}</h3>
                  {dock.dock_type && (
                    <span className="mt-0.5 inline-block rounded border border-line bg-white px-1.5 py-0.5 text-[10px] font-bold text-ink-soft">
                      {dock.dock_type}{dock.supports_refrigerated ? " · refrigerated" : ""}
                    </span>
                  )}
                </div>
                <span className="rounded-md border border-line bg-white px-2 py-0.5 font-mono text-xs font-bold" style={{ color }}>
                  {dock.dock_id.slice(0, 8)}
                </span>
              </div>

              <div className="space-y-2">
                {dockOptions.length === 0 ? (
                  <p className="py-2 text-center text-xs italic text-mist">No open slots right now</p>
                ) : (
                  (() => {
                    let lastDay = "";
                    return dockOptions.map((opt) => {
                      const thisDay = dayKey(opt.start_time);
                      const showDayLabel = thisDay !== lastDay;
                      lastDay = thisDay;
                      return (
                    <div key={opt.slot_id}>
                      {showDayLabel && (
                        <p className="mb-1 mt-3 text-[10px] font-bold uppercase tracking-wide text-mist first:mt-0">
                          {formatDayLabel(opt.start_time)}
                        </p>
                      )}
                    <div
                      className="rounded-xl border p-3 text-xs transition-all"
                      style={
                        opt.is_booked_by_me
                          ? { borderColor: "#059669", background: "#ecfdf5" }
                          : opt.is_held
                          ? { borderColor: color, background: `${color}0D` }
                          : !opt.is_compatible
                          ? { opacity: 0.6, borderColor: "var(--color-line)", background: "var(--color-cloud)" }
                          : { borderColor: "var(--color-line)", background: "white" }
                      }
                    >
                      <div className="mb-1 flex items-center justify-between font-bold">
                        <span className="flex items-center gap-1.5 text-sm font-extrabold text-ink">
                          <Clock className="h-4 w-4" style={{ color }} />
                          {new Date(opt.start_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} - {new Date(opt.end_time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                        </span>
                        <span
                          className="rounded-full px-2 py-0.5 text-[10px] font-bold uppercase"
                          style={
                            opt.is_booked_by_me
                              ? { background: "#d1fae5", color: "#047857" }
                              : opt.is_held
                              ? { background: `${color}1A`, color }
                              : opt.is_compatible
                              ? { background: "#ecfdf5", color: "#047857" }
                              : { background: "var(--color-line)", color: "var(--color-mist)" }
                          }
                        >
                          {opt.is_booked_by_me ? "your booking" : opt.is_held ? "held" : opt.is_compatible ? "open" : "unavailable"}
                        </span>
                      </div>

                      {!opt.is_compatible && !opt.is_booked_by_me && (
                        <p className="text-[11px] text-mist">{opt.compatibility_reason}</p>
                      )}

                      <div className="mt-2.5 flex gap-1.5">
                        {opt.is_booked_by_me && (
                          <span className="flex min-h-[40px] w-full items-center justify-center gap-1.5 rounded-xl bg-emerald-600/10 px-3 py-2 text-xs font-black text-emerald-700">
                            <CheckCircle2 className="h-4 w-4" /> Confirmed -- your dock appointment
                          </span>
                        )}
                        {!opt.is_booked_by_me && !opt.is_held && opt.is_compatible && hasConfirmedBooking && (
                          <button
                            onClick={() => void onRequestSlotChange(opt.slot_id)}
                            disabled={disabled}
                            className="flex min-h-[40px] w-full items-center justify-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-bold transition active:scale-95 disabled:opacity-50"
                            style={{ borderColor: `${color}4D`, background: `${color}1A`, color }}
                          >
                            <ArrowRightLeft className="h-3.5 w-3.5" /> Request this slot
                          </button>
                        )}
                        {!opt.is_booked_by_me && !opt.is_held && opt.is_compatible && !hasConfirmedBooking && (
                          <button
                            onClick={() => void onHoldSlot(opt.slot_id)}
                            disabled={disabled}
                            className="flex min-h-[40px] w-full items-center justify-center gap-1.5 rounded-xl border px-3 py-2 text-xs font-bold transition active:scale-95 disabled:opacity-50"
                            style={{ borderColor: `${color}4D`, background: `${color}1A`, color }}
                          >
                            <Lock className="h-3.5 w-3.5" /> Hold slot (5m)
                          </button>
                        )}
                        {!opt.is_booked_by_me && opt.is_held && (
                          <button
                            onClick={() => void onConfirmSlot(opt.slot_id)}
                            disabled={disabled}
                            className="flex min-h-[40px] w-full items-center justify-center gap-1.5 rounded-xl bg-emerald-600 px-3 py-2 text-xs font-black text-white shadow-soft transition hover:bg-emerald-500 active:scale-95 disabled:opacity-50"
                          >
                            <CheckCircle2 className="h-4 w-4" /> Confirm booking
                          </button>
                        )}
                      </div>
                    </div>
                    </div>
                      );
                    });
                  })()
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
